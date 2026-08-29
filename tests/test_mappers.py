"""Both known source shapes must reach the same canonical event.

The envelope circulated to W1 before normalisation moved to W2 is kept here
verbatim as a fixture. If it stops normalising, whoever built to it is broken
and this test says so before they find out at 03:00.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import mappers, schema, store  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ShapeDetectionTests(unittest.TestCase):
    def test_declared_schema_wins_over_inference(self):
        record = {"schema": "clearwave.attempt.v1", "amount": 1.0}
        self.assertEqual(mappers.detect_shape(record), "clearwave.attempt.v1")

    def test_minor_units_imply_the_pre_normalisation_envelope(self):
        self.assertEqual(
            mappers.detect_shape({"amount_minor": 100, "currency": "USD"}),
            "clearwave.attempt.v1",
        )

    def test_a_plain_canonical_record_is_recognised(self):
        self.assertEqual(mappers.detect_shape({"amount": 1.0, "occurred_at": "x"}), "canonical")

    def test_an_unregistered_shape_is_refused_not_guessed(self):
        with self.assertRaises(mappers.UnknownShape):
            mappers.to_canonical({"amount": 1.0}, shape="merchant-z-v9")


class AttemptV1Tests(unittest.TestCase):
    def setUp(self):
        self.native = json.loads(
            (FIXTURES / "native_attempt_v1.sample.json").read_text(encoding="utf-8")
        )

    def test_the_envelope_sent_to_w1_normalises(self):
        event = schema.normalise(mappers.to_canonical(self.native))
        self.assertEqual(event["payment_id"], "pay_8f21c")
        self.assertEqual(event["attempt_number"], 3)
        self.assertEqual(event["provider"], "P2")
        self.assertEqual(event["normalized_decline_reason"], "do_not_honor")

    def test_minor_units_become_major_units_before_conversion(self):
        # 1_899_000 COP minor units is 18_990.00 COP.
        event = schema.normalise(mappers.to_canonical(self.native))
        self.assertAlmostEqual(event["amount_usd"], 18_990.00 * 0.00025, places=6)

    def test_attempt_ts_is_accepted_as_event_time(self):
        self.assertNotIn("occurred_at", self.native)
        mapped = mappers.to_canonical(self.native)
        self.assertEqual(mapped["occurred_at"], self.native["attempt_ts"])

    def test_an_unregistered_currency_exponent_is_an_error(self):
        native = dict(self.native, currency="XYZ")
        with self.assertRaises(mappers.UnknownShape):
            mappers.to_canonical(native)

    def test_both_shapes_of_one_event_produce_the_same_canonical_row(self):
        native = dict(self.native)
        equivalent = {
            "event_id": native["event_id"],
            "payment_id": native["payment_id"],
            "attempt_id": native["attempt_id"],
            "attempt_number": native["attempt_number"],
            "occurred_at": native["attempt_ts"],
            "merchant_id": native["merchant_id"],
            "provider": native["provider"],
            "payment_method": native["payment_method"],
            "card_network": native["card_network"],
            "country": native["country"],
            "issuing_bank": native["issuing_bank"],
            "status": native["status"],
            "normalized_decline_reason": native["decline_reason"],
            "amount": 18_990.00,
            "currency": native["currency"],
            "latency_ms": native["latency_ms"],
        }
        from_native = schema.normalise(mappers.to_canonical(native))
        from_canonical = schema.normalise(mappers.to_canonical(equivalent))
        for field in ("payment_id", "attempt_id", "occurred_epoch", "provider",
                      "normalized_decline_reason", "amount_usd", "status"):
            self.assertEqual(from_native[field], from_canonical[field], field)


class IngestTests(unittest.TestCase):
    def test_ingest_accepts_a_mixed_batch_of_both_shapes(self):
        native = json.loads(
            (FIXTURES / "native_attempt_v1.sample.json").read_text(encoding="utf-8")
        )
        canonical = {
            "payment_id": "pay-1", "attempt_id": "att-1", "attempt_number": 1,
            "occurred_at": "2026-08-30T04:00:00Z", "merchant_id": "merchant-a",
            "provider": "provider-p3", "payment_method": "card", "card_network": "visa",
            "country": "MX", "issuing_bank": "bank-y", "status": "approved",
            "amount": 50.0, "currency": "USD",
        }
        connection = store.connect(":memory:")
        try:
            summary = store.ingest(connection, [native, canonical])
            self.assertEqual(summary["accepted"], 2)
            self.assertEqual(summary["rejected"], 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LoaderTests(unittest.TestCase):
    """The loader accepts every file shape a teammate is likely to hand us."""

    def setUp(self):
        import tempfile

        from detector import cli

        self.cli = cli
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.event = {"payment_id": "p1", "amount": 1.0}

    def _write(self, name: str, text: str) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_single_object_file(self):
        path = self._write("one.json", json.dumps(self.event))
        self.assertEqual(self.cli._load_events(path), [self.event])

    def test_array_file(self):
        path = self._write("many.json", json.dumps([self.event, self.event]))
        self.assertEqual(len(self.cli._load_events(path)), 2)

    def test_events_envelope(self):
        path = self._write("env.json", json.dumps({"events": [self.event]}))
        self.assertEqual(self.cli._load_events(path), [self.event])

    def test_json_lines(self):
        path = self._write("lines.jsonl", "\n".join([json.dumps(self.event)] * 3))
        self.assertEqual(len(self.cli._load_events(path)), 3)

    def test_garbage_is_refused_with_the_line_number(self):
        path = self._write("bad.jsonl", '{"a":1}\nnot json\n')
        with self.assertRaises(SystemExit) as caught:
            self.cli._load_events(path)
        self.assertIn(":2:", str(caught.exception))


class DeclineVocabularyTests(unittest.TestCase):
    """W1's frozen enum and C1b's closed vocabulary must not drift apart.

    This is the test that matters, not the individual mappings. The symptom of
    drift is silence: an unmapped native reason is dead-lettered, so the demo's
    guaranteed provider-degradation scenario simply loses its most telling
    attempts and the counts go quietly wrong. Reading W1's registered schema
    rather than restating it is the point - a value raul adds tomorrow fails
    here immediately instead of at 03:00.
    """

    W1_SCHEMA = (
        Path(__file__).resolve().parents[1]
        / "worker" / "registry" / "payment_attempt.schema.json"
    )

    def native_reasons(self) -> list[str]:
        document = json.loads(self.W1_SCHEMA.read_text(encoding="utf-8"))
        enum = document["properties"]["decline_reason"]["enum"]
        return [value for value in enum if isinstance(value, str)]

    def test_the_registered_schema_still_declares_the_vocabulary(self):
        # If this fails, W1 moved the enum and the drift guard below stopped
        # guarding anything.
        self.assertTrue(self.W1_SCHEMA.is_file(), f"{self.W1_SCHEMA} is missing")
        self.assertGreater(len(self.native_reasons()), 1)

    def test_every_native_reason_maps_to_the_closed_vocabulary(self):
        unmapped = [
            reason for reason in self.native_reasons()
            if reason not in mappers.NATIVE_DECLINE_REASONS
        ]
        self.assertEqual(
            unmapped, [],
            "W1 emits decline reason(s) W2 does not map, so those attempts would be "
            "dead-lettered and vanish from the counts. Add a mapping in "
            "detector/mappers.NATIVE_DECLINE_REASONS, or ask raul in STATUS.md when "
            "no canonical target is sensible - never guess one.",
        )

    def test_every_mapping_targets_a_canonical_reason(self):
        stray = sorted(
            target for target in set(mappers.NATIVE_DECLINE_REASONS.values())
            if target not in schema.DECLINE_REASONS
        )
        self.assertEqual(stray, [], "a mapping points outside DECLINE_REASONS")

    def test_the_map_carries_nothing_w1_does_not_emit(self):
        # A stale entry is drift in the other direction: it reads as coverage
        # nobody has.
        extra = sorted(set(mappers.NATIVE_DECLINE_REASONS) - set(self.native_reasons()))
        self.assertEqual(extra, [], "NATIVE_DECLINE_REASONS names codes W1 no longer emits")

    def test_provider_timeout_becomes_the_canonical_timeout(self):
        self.assertEqual(mappers.normalise_decline_reason("provider_timeout"), "timeout")

    def test_a_renamed_reason_keeps_its_native_spelling_as_evidence(self):
        mapped = mappers.to_canonical({
            "schema": "clearwave.attempt.v1",
            "status": "error",
            "decline_reason": "provider_timeout",
        })
        self.assertEqual(mapped["normalized_decline_reason"], "timeout")
        self.assertEqual(mapped["provider_raw_code"], "provider_timeout")

    def test_a_provider_raw_code_already_present_is_never_overwritten(self):
        mapped = mappers.to_canonical({
            "schema": "clearwave.attempt.v1",
            "status": "error",
            "decline_reason": "provider_timeout",
            "provider_raw_code": "504",
        })
        self.assertEqual(mapped["provider_raw_code"], "504")

    def test_an_unmapped_reason_is_refused_by_name_rather_than_guessed(self):
        with self.assertRaises(schema.InvalidEvent) as caught:
            schema.normalise(mappers.to_canonical({
                "schema": "clearwave.attempt.v1",
                "payment_id": "p1", "attempt_id": "a1", "attempt_number": 1,
                "attempt_ts": "2026-08-30T05:00:00Z", "merchant_id": "merchant-a",
                "provider": "stripe", "payment_method": "pse", "country": "CO",
                "status": "declined", "decline_reason": "gremlins",
                "amount_minor": 1000, "currency": "COP",
            }))
        self.assertIn("gremlins", str(caught.exception))

    def test_every_reason_w1_can_emit_survives_ingestion(self):
        """The end-to-end guard: no native reason lands in the dead-letter table."""
        connection = store.connect(":memory:")
        self.addCleanup(connection.close)
        events = [
            {
                "schema": "clearwave.attempt.v1",
                "event_id": f"evt-{index}",
                "payment_id": f"pay-{index}", "attempt_id": f"att-{index}",
                "attempt_number": 1, "attempt_ts": "2026-08-30T05:00:00.000Z",
                "merchant_id": "merchant-a", "provider": "stripe",
                "payment_method": "pse", "country": "CO", "issuing_bank": "bank-x",
                "status": "declined", "decline_reason": reason,
                "amount_minor": 25_000, "currency": "COP",
            }
            for index, reason in enumerate(self.native_reasons())
        ]
        summary = store.ingest(connection, events)
        dead = connection.execute("SELECT reason FROM dead_letter").fetchall()
        self.assertEqual(
            (summary["rejected"], [row["reason"] for row in dead]), (0, []),
        )
        self.assertEqual(summary["accepted"], len(events))
