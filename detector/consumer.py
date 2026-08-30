"""Live ingestion: W1's three Kafka topics into the canonical SQLite store.

Until this module existed the two halves of the product had never exchanged an
event. `detector ingest` took a path to a file; W1 published to a broker; nothing
joined them. This is that join, and it is deliberately the only new thing here -
normalisation, validation, dead-lettering, measurement and detection are all the
code that already ran on the file path.

Four properties carry the weight, in the order that getting them wrong would hurt.

**Counting is exactly-once over at-least-once delivery.** W1's own schema says
`event_id` is globally unique and that W2 dedupes on it. Every event table is
keyed on it and every insert is `INSERT OR IGNORE`, so a redelivered attempt is
counted once. Offsets are committed only after the batch is durably written, so a
crash replays a batch rather than losing it - which is safe precisely because
replay cannot double-count.

**There is one normalisation path.** Every record goes through
`detector/store.normalise_record`, the same function `ingest` calls, and an
unrecognised shape, currency or decline reason lands in `dead_letter` with its
reason. A visible rejection, never a silently wrong count.

**Event time, never arrival time.** Nothing here reads the clock. Buckets, windows
and onsets come from `attempt_ts`, `sample_ts` and `closed_ts`, so a replay of a
recorded stream reproduces the live run exactly.

**The broker is a seam, not a dependency.** `Source` is a three-method protocol.
`KafkaSource` implements it against a real broker and imports `confluent_kafka`
lazily, inside the constructor, so neither CI nor the file-based demo path ever
needs the library or a broker. Tests drive the loop through a fake source, the
way `investigation/gateway.py` takes an injectable runner.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol

from . import store

# The three topics W1 publishes, mapped to the record kind each one carries.
# Subscribing by explicit name rather than by pattern keeps a stray topic on the
# same broker from silently entering our counts.
TOPICS = {
    "payments.attempts": "attempt",
    "ops.telemetry": "telemetry",
    "payments.closed": "closed",
}

DEFAULT_GROUP_ID = "clearwave-detector"
BOOTSTRAP_ENV_VAR = "KAFKA_BOOTSTRAP_SERVERS"
DEFAULT_BOOTSTRAP = "localhost:9092"

# Confluent's wire format frames a serialised payload with a magic byte and a
# schema reference: version 0 is a four-byte big-endian schema id, version 1 a
# sixteen-byte GUID. W1 publishes through the Schema Registry JSON serializer,
# whose default is version 0; a value published by hand or by kcat carries no
# frame at all. All three are accepted, because the frame only names a schema
# and C1b validation is what actually decides whether a record may be counted.
#
# Reading the frame here rather than going through the registry's deserializer
# is deliberate: it keeps ingestion working when the registry is down, and it
# keeps the offline tests honest, since they can build the exact bytes W1 sends.
# `tests/test_consumer.py` asserts these constants against the library's own
# when it is installed, so the assumption cannot rot silently.
FRAME_LENGTHS = {0: 5, 1: 17}


@dataclass(frozen=True)
class Record:
    """One polled message, already decoded, or the reason it could not be.

    A message that cannot even be decoded is still a record: it carries its
    `error` and gets dead-lettered like any other rejection, because a payload
    we dropped without trace is indistinguishable from one that never arrived.
    """

    topic: str
    value: Any = None
    error: str | None = None
    raw: str | None = None


class Source(Protocol):
    """The seam between the loop and a broker.

    `poll` returns one record or None when nothing arrived within the timeout.
    `commit` advances the consumer group's offsets, and the loop calls it only
    after the store commit has returned.
    """

    def poll(self, timeout: float) -> Record | None: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


def decode(payload: bytes | str | None) -> Any:
    """Decode one message value, stripping the Schema Registry frame if present."""
    if payload is None:
        raise ValueError("message has no value (a tombstone reached a topic that has no keys to delete)")
    if isinstance(payload, str):
        return json.loads(payload)
    frame = FRAME_LENGTHS.get(payload[0] if payload else None)
    if frame is not None and len(payload) > frame:
        payload = payload[frame:]
    return json.loads(payload.decode("utf-8"))


@dataclass
class Progress:
    """What one consumer run did. Every number is a count of records, not bytes."""

    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    batches: int = 0
    polled: int = 0
    by_topic: dict[str, int] = field(default_factory=dict)

    def add(self, counts: dict[str, int]) -> None:
        self.accepted += counts["accepted"]
        self.duplicates += counts["duplicates"]
        self.rejected += counts["rejected"]
        self.batches += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "batches": self.batches,
            "polled": self.polled,
            "by_topic": dict(sorted(self.by_topic.items())),
        }


def consume(
    connection: sqlite3.Connection,
    source: Source,
    *,
    batch_size: int = 200,
    poll_timeout: float = 1.0,
    idle_polls: int = 3,
    max_messages: int | None = None,
    deadline: float | None = None,
    on_batch: Callable[[Progress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Progress:
    """Drain `source` into `connection` until it runs out, or a bound is reached.

    Returns when the source has been idle for `idle_polls` consecutive polls,
    when `max_messages` records have been handled, or when `deadline` (a
    `clock()` value) passes - whichever comes first. A run with no bound at all
    still terminates on idle, so the same function serves a one-shot sweep and a
    long-lived tail without a second code path.

    Two arguments turn the same loop into a service, and nothing else changes.
    `idle_polls` of zero or less means empty polls are never terminal: a quiet
    minute at 03:00 is not a reason for a running service to exit. `should_stop`
    is then the only way out, checked once per iteration, so a signal handler
    can ask for a stop without interrupting a batch mid-write. Either way the
    exit falls through to the same final `flush()`, so a stop drains what was
    already polled rather than dropping it.

    `on_batch` runs after every flush, including an empty one. Detection hooked
    there (`--detect-every`) is a wall-clock interval, not a traffic interval:
    a quiet minute still gets its sweep. The hook itself decides whether the
    interval has elapsed; this loop only guarantees it is asked.

    `clock` is injected for the same reason the source is: so a test can bound a
    run without sleeping. It measures the loop's own lifetime and never touches
    a record; event time comes from the payload alone.
    """
    progress = Progress()
    batch: list[tuple[str, Any]] = []
    idle = 0

    def flush() -> None:
        """Write, make durable, then advance offsets. That order is the contract."""
        if batch:
            progress.add(store.write_batch(connection, batch, source="kafka"))
            connection.commit()
            # Only now is a redelivery unnecessary. Committing before this line would
            # turn a crash into lost attempts; committing after it turns one into a
            # replay, and replay is free because every insert is idempotent.
            source.commit()
            batch.clear()
        if on_batch is not None:
            on_batch(progress)

    while True:
        if should_stop is not None and should_stop():
            break
        if deadline is not None and clock() >= deadline:
            break
        if max_messages is not None and progress.polled >= max_messages:
            break

        record = source.poll(poll_timeout)
        if record is None:
            flush()
            idle += 1
            if idle_polls > 0 and idle >= idle_polls:
                break
            continue

        idle = 0
        progress.polled += 1
        progress.by_topic[record.topic] = progress.by_topic.get(record.topic, 0) + 1
        batch.append(_pending(record))
        if len(batch) >= batch_size:
            flush()

    flush()
    return progress


def _pending(record: Record) -> tuple[str, Any]:
    """Turn one polled record into the ``(kind, raw)`` pair the store writes.

    An undecodable payload and an unknown topic both become records the store
    will refuse, rather than exceptions that stop the loop: one bad message must
    not be able to halt ingestion of a live stream.
    """
    if record.error is not None:
        return ("unroutable", {
            "reason": record.error,
            "topic": record.topic,
            "raw": record.raw,
        })
    kind = TOPICS.get(record.topic)
    if kind is None:
        return ("unroutable", {
            "reason": f"topic {record.topic!r} is not one of {sorted(TOPICS)}",
            "topic": record.topic,
            "value": record.value,
        })
    return (kind, record.value)


class KafkaSource:
    """`Source` over a real broker. The only part of W2 that knows Kafka exists.

    `confluent_kafka` is imported here rather than at module scope so that
    importing `detector` - which CI, the evidence tools and the file-based demo
    path all do - never requires the library or a broker.
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        group_id: str = DEFAULT_GROUP_ID,
        topics: tuple[str, ...] = tuple(TOPICS),
        from_beginning: bool = True,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:  # pragma: no cover - exercised by hand, not in CI
            raise SystemExit(
                "the Kafka consumer needs confluent-kafka: pip install -r detector/requirements.txt "
                "(or use `python3 -m detector seed` for the file-based path, which needs no broker)"
            ) from exc

        config = {
            "bootstrap.servers": bootstrap_servers
            or os.environ.get(BOOTSTRAP_ENV_VAR)
            or DEFAULT_BOOTSTRAP,
            "group.id": group_id,
            "auto.offset.reset": "earliest" if from_beginning else "latest",
            # Offsets advance only where this module says they do. Auto-commit
            # would acknowledge a message the store has not yet written.
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
        config.update(extra_config or {})
        self._consumer = Consumer(config)
        self._consumer.subscribe(list(topics))
        self._pending: list[Any] = []

    def poll(self, timeout: float) -> Record | None:
        message = self._consumer.poll(timeout)
        if message is None:
            return None
        if message.error() is not None:
            return Record(topic=message.topic() or "unknown", error=str(message.error()))
        self._pending.append(message)
        try:
            value = decode(message.value())
        except (ValueError, UnicodeDecodeError) as exc:
            return Record(
                topic=message.topic(),
                error=f"undecodable message value: {exc}",
                raw=repr(message.value())[:512],
            )
        return Record(topic=message.topic(), value=value)

    def commit(self) -> None:
        """Store the offsets of everything handled since the last commit, then commit.

        Synchronous on purpose: an asynchronous commit would return before the
        offsets are safe and reintroduce the window this ordering exists to close.
        """
        if not self._pending:
            return
        for message in self._pending:
            self._consumer.store_offsets(message)
        self._pending.clear()
        self._consumer.commit(asynchronous=False)

    def close(self) -> None:
        self._consumer.close()


class ReplaySource:
    """`Source` over an in-memory list of records. The offline demonstration.

    Same loop, same batching, same commit ordering, no broker. This is how the
    consumer's behaviour is shown when Kafka is not available, and how the tests
    drive it.
    """

    def __init__(self, records: list[Record]) -> None:
        self._records: Iterator[Record] = iter(records)
        self.commits = 0

    def poll(self, timeout: float) -> Record | None:
        return next(self._records, None)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        return None
