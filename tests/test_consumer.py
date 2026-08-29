"""Behavioural tests for the live Kafka consumer, driven without a broker.

The consumer is tested through its `Source` seam, the way `investigation`'s
gateway is tested through its injectable runner. Every message here is the byte
sequence W1 actually publishes - a Confluent Schema Registry frame around the
JSON of `worker/registry/*.schema.json` - so what these tests drive is the wire,
not a convenient paraphrase of it.

No test in this file needs a broker, a network or `confluent_kafka`, which is
the whole point: CI must stay offline while the behaviour that matters under
at-least-once delivery is still asserted.
"""

from __future__ import annotations

import json
import random
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector import cli, consumer, evidence, store  # noqa: E402
from tests import synthetic  # noqa: E402


def framed(payload: dict, schema_id: int = 1) -> bytes:
    """One value in Confluent's wire format: magic byte, schema id, then JSON."""
    return bytes([0]) + struct.pack(">I", schema_id) + json.dumps(payload).encode("utf-8")


def attempt_message(event: dict, index: int) -> consumer.Record:
    """Re-shape one canonical test event into the envelope W1 publishes.

    `tests/synthetic.py` stays the single generator - this only dresses its
    output in W1's `clearwave.attempt.v1` shape, including `provider_timeout`,
    which is the native spelling W1 emits under provider degradation and the one
    value whose mapping this repository got wrong.
    """
    reason = event.get("normalized_decline_reason")
    native = {
        "schema": "clearwave.attempt.v1",
        "event_id": f"evt-{index:06d}",
        "emitted_at": event["occurred_at"],
        "payment_id": event["payment_id"],
        "attempt_id": event["attempt_id"],
        "attempt_number": event["attempt_number"],
        "is_retry": event["attempt_number"] > 1,
        "attempt_ts": event["occurred_at"],
        "merchant_id": event["merchant_id"],
        "provider": event["provider"],
        "payment_method": event["payment_method"],
        "card_network": event.get("card_network"),
        "country": event["country"],
        "issuing_bank": event.get("issuing_bank"),
        "status": "error" if event["status"] == "timeout" else event["status"],
        "decline_reason": "provider_timeout" if reason == "timeout" else reason,
        "timed_out": event["status"] == "timeout",
        "amount_minor": int(round(event["amount"] * 100)),
        "currency": event["currency"],
        "latency_ms": event.get("latency_ms"),
        "service_id": "w1-worker-merchant-a",
        "deployment_id": "worker-local",
    }
    return consumer.Record(topic="payments.attempts", value=json.loads(framed(native)[5:]))


def attempt_records(events: list[dict]) -> list[consumer.Record]:
    return [attempt_message(event, index) for index, event in enumerate(events)]


TELEMETRY = {
    "schema": "clearwave.ops.v1",
    "event_id": "evt-ops-1",
    "emitted_at": "2026-08-30T05:15:00.000Z",
    "sample_ts": "2026-08-30T05:15:00.000Z",
    "service_id": "w1-worker-merchant-a",
    "deployment_id": "worker-local",
    "healthy": False,
    "queue_depth": 1800,
    "queue_delay_p95_ms": 2400,
    "cpu_pct": 91.4,
    "error_rate": 0.42,
    "restarts_total": 0,
}

CLOSED = {
    "schema": "clearwave.payment_closed.v1",
    "event_id": "evt-closed-1",
    "emitted_at": "2026-08-30T05:15:00.000Z",
    "payment_id": "pay-00001",
    "closed_ts": "2026-08-30T05:15:00.000Z",
    "outcome": "failed",
    "final_attempt_id": "att-00001-3",
    "total_attempts": 3,
    "merchant_id": "merchant-a",
    "country": "CO",
    "payment_method": "card",
    "amount_minor": 1_899_000,
    "currency": "COP",
}


def memory_store():
    connection = store.connect(":memory:")
    return connection


class TopicRoutingTests(unittest.TestCase):
    """All three topics land, each in the table its record kind belongs to."""

    def setUp(self):
        self.connection = memory_store()
        self.addCleanup(self.connection.close)

    def consumed(self, records):
        return consumer.consume(self.connection, consumer.ReplaySource(records), idle_polls=1)

    def test_each_topic_reaches_its_own_table(self):
        attempt = attempt_records(synthetic.healthy(minutes=1, per_minute=2))
        progress = self.consumed(attempt + [
            consumer.Record(topic="ops.telemetry", value=TELEMETRY),
            consumer.Record(topic="payments.closed", value=CLOSED),
        ])
        self.assertEqual(progress.rejected, 0)
        self.assertEqual(
            store.stored_counts(self.connection),
            {"attempt": len(attempt), "telemetry": 1, "closed": 1},
        )

    def test_a_topic_we_do_not_consume_is_refused_by_name(self):
        progress = self.consumed([consumer.Record(topic="payments.shadow", value={"a": 1})])
        reason = self.connection.execute("SELECT reason FROM dead_letter").fetchone()["reason"]
        self.assertEqual(progress.rejected, 1)
        self.assertIn("payments.shadow", reason)

    def test_an_undecodable_payload_is_dead_lettered_rather_than_dropped(self):
        progress = self.consumed([
            consumer.Record(topic="payments.attempts", error="undecodable message value: bad JSON")
        ])
        row = self.connection.execute("SELECT reason, source FROM dead_letter").fetchone()
        self.assertEqual(progress.rejected, 1)
        self.assertIn("undecodable", row["reason"])
        self.assertEqual(row["source"], "kafka")

    def test_one_bad_message_does_not_stop_the_stream(self):
        good = attempt_records(synthetic.healthy(minutes=1, per_minute=2))
        progress = self.consumed(
            good[:1] + [consumer.Record(topic="ops.telemetry", value={"nope": True})] + good[1:]
        )
        self.assertEqual((progress.accepted, progress.rejected), (len(good), 1))

    def test_event_time_comes_from_the_payload_and_never_from_arrival(self):
        event = synthetic.healthy(minutes=1, per_minute=1)[0]
        self.consumed(attempt_records([event]))
        stored = self.connection.execute("SELECT occurred_at FROM attempt").fetchone()
        self.assertEqual(stored["occurred_at"], "2026-08-30T04:00:00.000Z")


class WireFormatTests(unittest.TestCase):
    """W1 publishes through the Schema Registry serializer. That framing decodes."""

    def test_a_registry_framed_value_decodes(self):
        self.assertEqual(consumer.decode(framed({"a": 1})), {"a": 1})

    def test_a_plain_json_value_also_decodes(self):
        self.assertEqual(consumer.decode(b'{"a": 1}'), {"a": 1})

    def test_a_missing_value_is_an_error_rather_than_an_empty_event(self):
        with self.assertRaises(ValueError):
            consumer.decode(None)

    def test_a_truncated_frame_is_an_error_rather_than_a_guess(self):
        with self.assertRaises(ValueError):
            consumer.decode(bytes([0, 0, 0, 0, 1]) + b"not json")

    def test_the_guid_framing_decodes_too(self):
        value = bytes([1]) + bytes(16) + b'{"a": 1}'
        self.assertEqual(consumer.decode(value), {"a": 1})

    def test_our_framing_matches_the_client_library_when_it_is_installed(self):
        """A drift guard, skipped where the client is absent - which is CI.

        Our decoder reads the frame itself rather than going through the
        registry deserializer, so the one thing that could rot is the framing
        constant. Where the library is present, this pins it to the library's.
        """
        try:
            from confluent_kafka.schema_registry import _MAGIC_BYTE_V0, _MAGIC_BYTE_V1
            from confluent_kafka.schema_registry.common.serde import SchemaId
        except ImportError:
            self.skipTest("confluent-kafka is not installed in this environment")
        pinned = {
            _MAGIC_BYTE_V0: len(SchemaId("JSON", 7).id_to_bytes()),
            _MAGIC_BYTE_V1: len(SchemaId("JSON", 7, "00000000-0000-0000-0000-000000000000").guid_to_bytes()),
        }
        self.assertEqual(consumer.FRAME_LENGTHS, pinned)


class DeduplicationTests(unittest.TestCase):
    """At-least-once delivery must not become at-least-once counting."""

    def setUp(self):
        self.connection = memory_store()
        self.addCleanup(self.connection.close)
        self.events = synthetic.healthy(minutes=2, per_minute=5)

    def test_a_redelivered_event_is_counted_once(self):
        records = attempt_records(self.events)
        progress = consumer.consume(
            self.connection, consumer.ReplaySource(records + records), idle_polls=1
        )
        self.assertEqual(progress.polled, 2 * len(records))
        self.assertEqual(progress.accepted, len(records))
        self.assertEqual(progress.duplicates, len(records))
        self.assertEqual(store.stored_counts(self.connection)["attempt"], len(records))

    def test_a_replayed_run_adds_nothing(self):
        records = attempt_records(self.events)
        consumer.consume(self.connection, consumer.ReplaySource(records), idle_polls=1)
        before = store.stored_counts(self.connection)
        consumer.consume(self.connection, consumer.ReplaySource(records), idle_polls=1)
        self.assertEqual(store.stored_counts(self.connection), before)

    def test_telemetry_and_closed_records_dedupe_on_event_id_too(self):
        records = [
            consumer.Record(topic="ops.telemetry", value=TELEMETRY),
            consumer.Record(topic="payments.closed", value=CLOSED),
        ]
        consumer.consume(self.connection, consumer.ReplaySource(records * 3), idle_polls=1)
        counts = store.stored_counts(self.connection)
        self.assertEqual((counts["telemetry"], counts["closed"]), (1, 1))


class OffsetOrderingTests(unittest.TestCase):
    """A crash must replay a batch, never lose one.

    The store commit has to be durable *before* offsets advance. These tests
    prove that by reading the file from a second connection at the moment the
    source is asked to commit: uncommitted rows are invisible there, so a count
    of zero at that point would mean the offsets were about to acknowledge data
    that is not yet safe.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "clearwave.db"
        self.connection = store.connect(self.path)
        self.addCleanup(self.connection.close)
        self.records = attempt_records(synthetic.healthy(minutes=1, per_minute=4))

    def observer_count(self) -> int:
        """How many attempts a *second* reader can see right now.

        A plain read-only connection with a short timeout, not `store.connect`:
        that one runs the schema script, which needs a write lock and would
        block behind the consumer's open transaction instead of answering. The
        whole point is to observe what is durable without waiting for it.
        """
        observer = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2.0)
        try:
            return observer.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
        finally:
            observer.close()

    def test_offsets_advance_only_over_rows_another_reader_can_already_see(self):
        seen: list[int] = []

        class WatchingSource(consumer.ReplaySource):
            def commit(inner) -> None:  # noqa: N805 - inner self, outer test
                seen.append(self.observer_count())
                super(WatchingSource, inner).commit()

        source = WatchingSource(self.records)
        consumer.consume(self.connection, source, idle_polls=1)
        self.assertEqual(seen, [len(self.records)])

    def test_a_failed_write_leaves_the_offsets_where_they_were(self):
        committed: list[str] = []

        class WatchingSource(consumer.ReplaySource):
            def commit(inner) -> None:  # noqa: N805
                committed.append("offsets")

        def failing(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        original = store.write_batch
        store.write_batch = failing
        self.addCleanup(setattr, store, "write_batch", original)
        # The store error must surface, not be swallowed into a quiet partial run.
        with self.assertRaises(sqlite3.OperationalError):
            consumer.consume(self.connection, WatchingSource(self.records), idle_polls=1)
        self.assertEqual(committed, [], "offsets advanced over a batch that was never written")
        self.assertEqual(self.observer_count(), 0)

    def test_each_full_batch_commits_its_own_offsets(self):
        source = consumer.ReplaySource(self.records)
        progress = consumer.consume(self.connection, source, batch_size=2, idle_polls=1)
        self.assertEqual(progress.batches, source.commits)
        self.assertEqual(source.commits, len(self.records) // 2)


class BoundsTests(unittest.TestCase):
    """A run must terminate on its own, without a broker to go quiet for it."""

    def setUp(self):
        self.connection = memory_store()
        self.addCleanup(self.connection.close)
        self.records = attempt_records(synthetic.healthy(minutes=1, per_minute=10))

    def test_max_messages_stops_the_run(self):
        progress = consumer.consume(
            self.connection, consumer.ReplaySource(self.records), max_messages=3, idle_polls=1
        )
        self.assertEqual(progress.polled, 3)

    def test_a_deadline_stops_the_run_without_sleeping(self):
        ticks = iter(range(100))
        progress = consumer.consume(
            self.connection,
            consumer.ReplaySource(self.records),
            deadline=4,
            idle_polls=1,
            clock=lambda: next(ticks),
        )
        self.assertEqual(progress.polled, 4)
        # Everything polled before the deadline is still durably written.
        self.assertEqual(store.stored_counts(self.connection)["attempt"], 4)

    def test_an_empty_source_is_a_well_formed_run_and_not_an_error(self):
        progress = consumer.consume(self.connection, consumer.ReplaySource([]), idle_polls=1)
        self.assertEqual(progress.as_dict()["polled"], 0)
        self.assertEqual(progress.batches, 0)


class LivePipelineTests(unittest.TestCase):
    """The acceptance case: W1's topics in, a priced C3 record out."""

    @classmethod
    def setUpClass(cls):
        cls.events = synthetic.with_provider_incident()
        cls.records = attempt_records(cls.events)

    def consumed_store(self, records=None):
        connection = memory_store()
        self.addCleanup(connection.close)
        consumer.consume(connection, consumer.ReplaySource(records or self.records), idle_polls=1)
        return connection

    def test_the_provider_timeout_retries_survive_the_wire(self):
        connection = self.consumed_store()
        timeouts = connection.execute(
            "SELECT COUNT(*) AS n FROM attempt WHERE status = 'timeout' "
            "AND normalized_decline_reason = 'timeout'"
        ).fetchone()["n"]
        dead = connection.execute("SELECT COUNT(*) AS n FROM dead_letter").fetchone()["n"]
        self.assertGreater(timeouts, 0, "the retry storm's timeouts were lost in normalisation")
        self.assertEqual(dead, 0)
        raw = connection.execute(
            "SELECT DISTINCT provider_raw_code FROM attempt WHERE status = 'timeout'"
        ).fetchone()["provider_raw_code"]
        self.assertEqual(raw, "provider_timeout", "the native code must survive as evidence")

    def test_detection_runs_on_consumed_traffic_and_prices_the_incident(self):
        connection = self.consumed_store()
        result = cli._sweep(connection, 5, persist=True)
        incident = result["incident"]
        self.assertIsNotNone(incident, "live traffic produced no incident")
        self.assertTrue(result["stored"])
        self.assertGreater(incident["financial_impact"]["gmv_at_risk"]["amount"], 0)
        self.assertEqual(incident["affected_cohort"]["provider"], "provider-p2")

    def test_the_incident_is_handed_over_as_detected(self):
        connection = self.consumed_store()
        incident = cli._sweep(connection, 5, persist=True)["incident"]
        stored = store.load_incident(connection, incident["incident_id"])
        self.assertEqual(stored["lifecycle_state"], "detected")

    def test_the_same_events_in_another_arrival_order_give_the_same_incident(self):
        shuffled = list(self.records)
        random.Random(11).shuffle(shuffled)
        first = cli._sweep(self.consumed_store(), 5, persist=False)
        second = cli._sweep(self.consumed_store(shuffled), 5, persist=False)
        self.assertEqual(
            json.dumps(first, sort_keys=True, default=str),
            json.dumps(second, sort_keys=True, default=str),
        )


class RuntimeHealthTests(unittest.TestCase):
    """`ops.telemetry` is the only first-party source of service-level gauges."""

    def setUp(self):
        self.connection = memory_store()
        self.addCleanup(self.connection.close)
        self.records = attempt_records(synthetic.healthy(minutes=80, per_minute=4))
        self.request = {
            "target": {"kind": "service", "service": "w1-worker-merchant-a"},
            "window": {"start": "2026-08-30T04:00:00Z", "end": "2026-08-30T05:30:00Z"},
        }

    def answered(self):
        return evidence.answer("operational_metrics", self.request, self.connection)

    def test_without_a_sample_the_tool_still_says_unobserved(self):
        consumer.consume(self.connection, consumer.ReplaySource(self.records), idle_polls=1)
        health = self.answered()["runtime_health"]
        self.assertEqual(health["status"], "unobserved")
        self.assertIn("no operational telemetry sample", health["reason"])

    def test_a_consumed_sample_becomes_measured_runtime_health(self):
        consumer.consume(
            self.connection,
            consumer.ReplaySource(
                self.records + [consumer.Record(topic="ops.telemetry", value=TELEMETRY)]
            ),
            idle_polls=1,
        )
        health = self.answered()["runtime_health"]
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["samples"], 1)
        self.assertEqual(health["cpu_pct_peak"], 91.4)
        self.assertEqual(health["observed_deployment_ids"], ["worker-local"])

    def test_service_health_stays_derived_from_attempts_and_is_not_overwritten(self):
        consumer.consume(
            self.connection,
            consumer.ReplaySource(
                self.records + [consumer.Record(topic="ops.telemetry", value=TELEMETRY)]
            ),
            idle_polls=1,
        )
        answer = self.answered()
        self.assertEqual(answer["service_health"]["status"], "healthy")
        self.assertIn("derived from first-party attempts", answer["service_health"]["criterion"])


class OfflineTests(unittest.TestCase):
    """CI has no broker and no `confluent_kafka`. Importing us must not need one."""

    def test_importing_the_detector_pulls_in_no_kafka_client(self):
        self.assertNotIn("confluent_kafka", sys.modules)

    def test_the_file_based_path_is_untouched_by_any_of_this(self):
        connection = memory_store()
        self.addCleanup(connection.close)
        summary = store.ingest(connection, synthetic.healthy(minutes=1, per_minute=3))
        self.assertEqual((summary["accepted"], summary["rejected"]), (3, 0))
        self.assertIsNotNone(cli._sweep(connection, 5, persist=False))


if __name__ == "__main__":
    unittest.main()
