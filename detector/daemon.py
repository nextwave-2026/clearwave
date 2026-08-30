"""Continuous detection process: the consume loop, made not to stop.

Derek's 2026-08-30T05:10Z ruling is that the demo is a product, not a terminal
session: traffic, detection, investigation and notification all run by
themselves. Traffic and investigation already did. Detection did not - it ran
only while somebody held down `python3 -m detector consume`. This module is the
operator surface that closes that gap, and it is deliberately thin.

Nothing here detects. `consumer.consume` already polls, writes durably, commits
offsets in that order, and sweeps on `--detect-every` through the batch hook
`cli._periodic_sweeper`. The only things missing for a service were a loop that
does not treat a quiet poll as the end of the world and a way to ask it to stop.
Both live in `consumer.consume` now (`idle_polls=0` and `should_stop`); this
module loads the arguments, arms the signals and closes the source.

Signal handling mirrors `investigation/daemon.py` deliberately - that is the
pattern this repository set for a compose service, and matching it is the point.
A handler sets a `threading.Event` rather than raising, so the loop leaves at the
top of an iteration and falls through to the final `flush()`. A batch already
polled is written and its offsets committed before the process exits; a SIGTERM
from `docker compose stop` drains rather than dropping a partial batch.

**Offsets still commit only after the SQLite write is durable** (DECISIONS.md,
andres 21:30Z). Nothing in this module touches that ordering. At-least-once is
safe because every event table is keyed on `event_id` with `INSERT OR IGNORE`,
so a restart replays a batch and counts it once; acknowledging on receipt would
turn a crash into permanently missing attempts.

**A transient poll error does not reach this loop.** A message-level Kafka error
already becomes a `Record` carrying its reason, is dead-lettered like any other
rejection and the loop continues - one bad message cannot halt a live stream. A
failure below that, where the client itself cannot continue, is allowed to exit
non-zero into `restart: unless-stopped`. That is the deliberate choice: compose
already owns process restart, and a second retry policy in here would be a
supervisor we do not need and would have to keep honest.

**No backfill on first start.** The consumer starts at each topic's beginning by
default, so a fresh store rebuilds from whatever the broker still retains; that,
plus a stack left warm, is what gives merchant-relative severity the six hours of
history it needs. `detector ingest --stream` remains the operator's path for
W1's 83 MB replay file, which is not in the repository and cannot be assumed
present in a container.
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path
from types import FrameType
from typing import Any, Callable

from . import consumer, store

DEFAULT_DB = Path("state/clearwave.db")
DEFAULT_DETECT_EVERY_SECONDS = 45.0
DEFAULT_BATCH_SIZE = 200
DEFAULT_POLL_TIMEOUT_SECONDS = 1.0


def resolve_db(path: str | None) -> Path:
    """Store path: ``--db``, else ``CLEARWAVE_DB``, else ``state/clearwave.db``."""
    if path:
        return Path(path)
    env = os.environ.get("CLEARWAVE_DB")
    if env:
        return Path(env)
    return Path(DEFAULT_DB)


def arm_stop(event: threading.Event, reason_holder: dict[str, str]) -> dict[int, Any]:
    """Install SIGINT and SIGTERM handlers that set ``event`` instead of raising."""
    previous = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }

    def handle(signum: int, _frame: FrameType | None) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        reason_holder["reason"] = name
        event.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    return previous


def restore_signals(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def serve(
    db_path: Path | str,
    *,
    bootstrap_servers: str | None = None,
    group_id: str = consumer.DEFAULT_GROUP_ID,
    topics: tuple[str, ...] = tuple(sorted(consumer.TOPICS)),
    from_latest: bool = False,
    detect_every_seconds: float = DEFAULT_DETECT_EVERY_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    poll_timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    stop_event: threading.Event | None = None,
    source_factory: Callable[[], consumer.Source] | None = None,
    install_signal_handlers: bool = True,
) -> int:
    """Consume and sweep until SIGINT or SIGTERM. Empty polls are not terminal.

    `source_factory` is injected for the same reason `consumer.Source` exists at
    all: the tests drive the whole lifecycle - start, stop, drain - with no
    broker anywhere.
    """
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    if poll_timeout <= 0:
        raise ValueError("poll timeout must be positive")

    resolved = Path(db_path).resolve()
    os.environ["CLEARWAVE_DB"] = str(resolved)
    connection = store.connect(resolved)

    # Imported here, not at module scope: `cli` imports this module for its
    # `daemon` subcommand, and the sweeper is the one thing we borrow back.
    from .cli import _periodic_sweeper

    event = stop_event or threading.Event()
    reason_holder: dict[str, str] = {}
    previous: dict[int, Any] | None = None
    if install_signal_handlers:
        previous = arm_stop(event, reason_holder)

    factory = source_factory or (
        lambda: consumer.KafkaSource(
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            topics=tuple(topics),
            from_beginning=not from_latest,
        )
    )
    source = factory()
    sweeps: list[dict[str, Any]] = []
    try:
        print(
            f"detector daemon watching {resolved} topics={','.join(topics)} "
            f"detect-every={detect_every_seconds}s group={group_id}",
            flush=True,
        )
        progress = consumer.consume(
            connection,
            source,
            batch_size=batch_size,
            poll_timeout=poll_timeout,
            # Zero means a quiet topic is not a reason to exit. `should_stop` is
            # then the only way out, and it is set by the signal handlers.
            idle_polls=0,
            on_batch=_periodic_sweeper(connection, detect_every_seconds, sweeps),
            should_stop=event.is_set,
        )
        if reason_holder.get("reason"):
            print(f"detector daemon stopping on {reason_holder['reason']}", flush=True)
        print(
            "detector daemon drained "
            f"accepted={progress.accepted} duplicates={progress.duplicates} "
            f"rejected={progress.rejected} batches={progress.batches} "
            f"sweeps={len(sweeps)}",
            flush=True,
        )
        return 0
    finally:
        if previous is not None:
            restore_signals(previous)
        source.close()
        connection.close()
        print("detector daemon stopped", flush=True)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Define the `daemon` subcommand's arguments on an existing parser."""
    parser.add_argument(
        "--bootstrap-servers",
        default=None,
        help=f"Kafka bootstrap servers (default: ${consumer.BOOTSTRAP_ENV_VAR} "
             f"or {consumer.DEFAULT_BOOTSTRAP})",
    )
    parser.add_argument(
        "--group-id", default=consumer.DEFAULT_GROUP_ID,
        help="consumer group; the same group resumes where it left off",
    )
    parser.add_argument(
        "--topics", nargs="+", default=sorted(consumer.TOPICS),
        choices=sorted(consumer.TOPICS),
        help="which of W1's topics to read (default: all three)",
    )
    parser.add_argument(
        "--from-latest", action="store_true",
        help="start at the end of each topic instead of replaying it from the "
             "beginning. The default replays, because a store that starts empty "
             "needs history before merchant-relative severity means anything.",
    )
    parser.add_argument(
        "--detect-every", type=float, default=DEFAULT_DETECT_EVERY_SECONDS,
        metavar="SECONDS",
        help=f"seconds between detection sweeps (default: {DEFAULT_DETECT_EVERY_SECONDS})",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help="records written per transaction before offsets advance",
    )


def run(args: argparse.Namespace) -> int:
    """Bridge from the parsed `detector daemon` namespace into `serve`."""
    return serve(
        resolve_db(args.db),
        bootstrap_servers=args.bootstrap_servers,
        group_id=args.group_id,
        topics=tuple(args.topics),
        from_latest=args.from_latest,
        detect_every_seconds=args.detect_every,
        batch_size=args.batch_size,
    )
