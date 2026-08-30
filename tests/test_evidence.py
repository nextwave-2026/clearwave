"""Behavioural tests for the C2 evidence tools.

Each test asserts something a consumer actually depends on: that an empty
store answers honestly instead of crashing or borrowing a fixture, that the
number a tool reports is the number the detector reports, that a cited
`query_id` still resolves to the same call, and that replaying the same events
in another order produces the same answer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector import cli, config, detect, evidence, metrics, store  # noqa: E402
from tests import synthetic  # noqa: E402

TOOL_DIR = ROOT / "stubs" / "evidence"
WINDOW = {"start": "2026-08-30T05:14:00Z", "end": "2026-08-30T05:19:00Z"}
SERIES_WINDOW = {"start": "2026-08-30T04:00:00Z", "end": "2026-08-30T05:19:00Z"}
COHORT = {"provider": "provider-p2"}

# One representative call per measured tool, in the shape the contract
# publishes. `external_status` is absent on purpose: W3 implements it and it
# stays on its fixture.
CALLS = {
    "cohort_metrics": {"cohort": COHORT, "window": WINDOW},
    "cohort_compare": {
        "cohort": {"provider": "provider-p2", "country": "CO"},
        "window": WINDOW,
        "compare_dimensions": ["provider", "country"],
    },
    "drilldown": {"incident_id": "inc-2026-08-30-unknown", "window": WINDOW},
    "decline_breakdown": {"cohort": COHORT, "window": WINDOW},
    "retry_stats": {"cohort": COHORT, "window": WINDOW},
    "operational_metrics": {"target": {"kind": "cohort", **COHORT}, "window": WINDOW},
    "confounding_check": {
        "dimension_a": "provider",
        "dimension_b": "issuing_bank",
        "window": WINDOW,
    },
    "incident_history": {"merchant_id": "merchant-a"},
    "financial_impact": {"incident_id": "inc-2026-08-30-unknown"},
    "metric_series": {"cohort": COHORT, "window": SERIES_WINDOW},
    "ingest_health": {},
}

# One valid record of each non-canonical kind, in W1's published wire shape.
# `ingest_health` reports their newest event times separately from the
# watermark, which is cut from the attempt stream alone.
TELEMETRY_SAMPLE = {
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

CLOSED_PAYMENT = {
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

_OPEN: list = []


def loaded(events=None):
    """A fresh in-memory store, optionally with events already ingested."""
    connection = store.connect(":memory:")
    _OPEN.append(connection)
    if events:
        store.ingest(connection, events)
    return connection


def detected(connection):
    """Run one detection sweep the way the CLI does and persist the record."""
    bounds = store.window_bounds(connection)
    end = evidence.watermark(connection) + config.BUCKET_SECONDS
    start = max(bounds[0], end - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS)
    incident = detect.build_incident(connection, start, end)
    incident["incident_id"] = cli.incident_id_for(incident)
    store.save_incident(connection, incident)
    return incident


def tearDownModule():
    while _OPEN:
        _OPEN.pop().close()


class EmptyStoreTests(unittest.TestCase):
    """CI drives every tool with no measured data. That must be an answer."""

    def setUp(self):
        self.connection = loaded()

    def test_every_tool_answers_over_an_empty_store(self):
        for tool, request in CALLS.items():
            with self.subTest(tool=tool):
                response = evidence.answer(tool, request, self.connection)
                self.assertTrue(response["as_of"], "every response carries an as_of")

    def test_counters_are_zero_and_undefined_rates_are_null(self):
        response = evidence.answer("cohort_metrics", CALLS["cohort_metrics"], self.connection)
        self.assertEqual(response["payment_metrics"]["attempted_payments"], 0)
        self.assertEqual(response["attempt_metrics"]["attempts"], 0)
        self.assertIsNone(response["payment_metrics"]["approval_conversion"])
        self.assertEqual(response["volume"]["attempted"]["amount"], 0.0)
        self.assertEqual(response["decline_mix"], [])

    def test_no_fixture_number_leaks_into_a_measured_response(self):
        """The published fixture says 1000 payments. An empty store says none."""
        fixture = json.loads(
            (ROOT / "stubs" / "fixtures" / "cohort_metrics.json").read_text(encoding="utf-8")
        )
        response = evidence.answer("cohort_metrics", CALLS["cohort_metrics"], self.connection)
        self.assertEqual(fixture["response"]["payment_metrics"]["attempted_payments"], 1000)
        self.assertNotEqual(
            response["payment_metrics"]["attempted_payments"],
            fixture["response"]["payment_metrics"]["attempted_payments"],
        )

    def test_an_unknown_incident_is_an_empty_path_and_a_stated_reason(self):
        response = evidence.answer("drilldown", {"incident_id": "inc-nope"}, self.connection)
        self.assertEqual(response["levels"], [])
        self.assertIn("inc-nope", response["stop_reason"])

    def test_an_unknown_incident_claims_no_money(self):
        response = evidence.answer("financial_impact", {"incident_id": "inc-nope"}, self.connection)
        self.assertEqual(response["gmv_at_risk"]["amount"], 0.0)
        self.assertIsNone(response["expected_approval_rate"])
        self.assertIn("inc-nope", " ".join(response["assumptions"]))

    def test_confounding_says_it_cannot_tell_rather_than_claiming_separation(self):
        response = evidence.answer(
            "confounding_check", CALLS["confounding_check"], self.connection
        )
        self.assertFalse(response["structurally_inseparable"])
        self.assertEqual(response["cross_tabulation"]["rows"], [])
        self.assertIn("cannot be established", response["interpretation"])



class IngestHealthTests(unittest.TestCase):
    """The tool that answers "is this actually live?" must not be able to lie."""

    def test_an_empty_store_reports_nothing_ingested_rather_than_failing(self):
        response = evidence.answer("ingest_health", {}, loaded())
        self.assertEqual(response["accepted"], 0)
        self.assertEqual(response["rejected"], 0)
        self.assertEqual(response["dead_letter"]["count"], 0)
        self.assertEqual(response["dead_letter"]["reasons"], [])
        self.assertIsNone(response["newest_event_at"])
        self.assertIsNone(response["lag_seconds"])

    def test_accepted_is_the_row_count_the_store_actually_holds(self):
        connection = loaded(synthetic.with_provider_incident())
        response = evidence.answer("ingest_health", {}, connection)
        stored = connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"]
        self.assertEqual(response["accepted"], stored)
        self.assertEqual(response["stored"]["attempts"], stored)
        self.assertGreater(response["accepted"], 0)

    def test_a_redelivered_record_is_not_counted_twice(self):
        """Exactly-once counting over at-least-once delivery, seen from outside."""
        events = synthetic.with_provider_incident()
        connection = loaded(events)
        once = evidence.answer("ingest_health", {}, connection)["accepted"]
        store.ingest(connection, events)
        self.assertEqual(evidence.answer("ingest_health", {}, connection)["accepted"], once)

    def test_a_refused_record_is_reported_with_its_reason(self):
        connection = loaded()
        store.write_batch(connection, [("attempt", {"not": "a payment"})])
        connection.commit()
        response = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(response["accepted"], 0)
        self.assertEqual(response["rejected"], 1)
        self.assertEqual(response["dead_letter"]["count"], 1)
        self.assertEqual(response["dead_letter"]["distinct_reasons"], 1)
        self.assertEqual(response["dead_letter"]["reasons"][0]["count"], 1)
        self.assertTrue(response["dead_letter"]["reasons"][0]["reason"])
        self.assertEqual(
            response["dead_letter"]["by_source"], [{"source": "ingest", "count": 1}]
        )

    def test_rejected_and_the_dead_letter_count_never_disagree(self):
        connection = loaded(synthetic.with_provider_incident())
        store.write_batch(connection, [("attempt", {"a": 1}), ("telemetry", {"b": 2})])
        connection.commit()
        response = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(response["rejected"], response["dead_letter"]["count"])
        self.assertEqual(response["rejected"], 2)

    def test_the_reason_list_is_bounded_but_the_distinct_count_is_not(self):
        connection = loaded()
        limit = evidence.DEAD_LETTER_REASON_LIMIT
        for index in range(limit + 3):
            connection.execute(
                "INSERT INTO dead_letter (reason, payload, source) VALUES (?, ?, ?)",
                (f"reason-{index}", "{}", "ingest"),
            )
        connection.commit()
        response = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(len(response["dead_letter"]["reasons"]), limit)
        self.assertEqual(response["dead_letter"]["distinct_reasons"], limit + 3)
        self.assertEqual(response["rejected"], limit + 3)

    def test_lag_is_event_time_against_the_watermark_and_not_the_clock(self):
        connection = loaded(synthetic.with_provider_incident())
        response = evidence.answer("ingest_health", {}, connection)
        bounds = store.window_bounds(connection)
        self.assertEqual(
            response["lag_seconds"], bounds[1] - evidence.watermark(connection)
        )
        self.assertEqual(response["newest_event_at"], evidence.schema.iso_utc(bounds[1]))
        self.assertEqual(response["oldest_event_at"], evidence.schema.iso_utc(bounds[0]))
        self.assertEqual(
            response["watermark"], evidence.schema.iso_utc(evidence.watermark(connection))
        )
        self.assertEqual(response["as_of"], response["watermark"])
        self.assertEqual(
            response["lateness_grace_seconds"], config.LATENESS_GRACE_SECONDS
        )

    def test_a_store_holding_only_telemetry_does_not_report_nothing_observed(self):
        """The watermark is the attempt stream, so telemetry needs its own reading."""
        connection = loaded()
        store.write_batch(connection, [("telemetry", TELEMETRY_SAMPLE)])
        connection.commit()
        response = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(response["rejected"], 0, "the sample must normalise, not dead-letter")
        self.assertEqual(response["stored"]["telemetry_samples"], 1)
        # The canonical stream is genuinely empty, and says so.
        self.assertEqual(response["accepted"], 0)
        self.assertIsNone(response["newest_event_at"])
        self.assertIsNone(response["newest_by_kind"]["attempts"])
        # But the store is not silent about what it does hold.
        self.assertEqual(response["newest_by_kind"]["telemetry_samples"], "2026-08-30T05:15:00Z")

    def test_a_closed_payment_gets_its_own_reading_too(self):
        connection = loaded()
        store.write_batch(connection, [("closed", CLOSED_PAYMENT)])
        connection.commit()
        response = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(response["rejected"], 0)
        self.assertEqual(response["newest_by_kind"]["payments_closed"], "2026-08-30T05:15:00Z")
        self.assertIsNone(response["newest_by_kind"]["telemetry_samples"])

    def test_a_newer_telemetry_sample_never_moves_the_watermark(self):
        """`as_of` means the same thing on every C2 tool. This one may not redefine it."""
        connection = loaded(synthetic.with_provider_incident())
        before = evidence.answer("ingest_health", {}, connection)
        store.write_batch(connection, [("telemetry", {**TELEMETRY_SAMPLE, "sample_ts": "2027-01-01T00:00:00Z"})])
        connection.commit()
        after = evidence.answer("ingest_health", {}, connection)
        self.assertEqual(after["watermark"], before["watermark"])
        self.assertEqual(after["as_of"], before["as_of"])
        self.assertEqual(after["newest_event_at"], before["newest_event_at"])
        self.assertEqual(after["lag_seconds"], before["lag_seconds"])
        self.assertEqual(after["newest_by_kind"]["telemetry_samples"], "2027-01-01T00:00:00Z")

    def test_duplicates_is_named_as_unmeasured_rather_than_invented(self):
        response = evidence.answer("ingest_health", {}, loaded())
        self.assertIn("duplicates", response["not_measured"])
        self.assertNotIn("duplicates", response)

    def test_an_input_is_refused_rather_than_silently_widened(self):
        with self.assertRaises(evidence.EvidenceError) as raised:
            evidence.answer("ingest_health", {"cohort": {"provider": "p"}}, loaded())
        self.assertEqual(raised.exception.code, "invalid_input")
        self.assertIn("cohort", raised.exception.message)

    def test_the_same_events_in_a_different_order_give_the_same_answer(self):
        events = synthetic.with_provider_incident()
        forward = evidence.answer("ingest_health", {}, loaded(events))
        backward = evidence.answer("ingest_health", {}, loaded(list(reversed(events))))
        self.assertEqual(forward, backward)


class MeasuredAnswerTests(unittest.TestCase):
    """A tool and the detector must never give two answers to one question."""

    @classmethod
    def setUpClass(cls):
        cls.connection = loaded(synthetic.with_provider_incident())
        cls.incident = detected(cls.connection)
        cls.window = cls.incident["detection"]["window"]

    def _window(self):
        return {
            "start": evidence.schema.iso_utc(self.window["start_epoch"]),
            "end": evidence.schema.iso_utc(self.window["end_epoch"]),
        }

    def test_cohort_metrics_reports_what_the_measurement_layer_measures(self):
        cohort = self.incident["affected_cohort"]
        measured = metrics.payment_metrics(
            self.connection, cohort, self.window["start_epoch"], self.window["end_epoch"]
        )
        response = evidence.answer(
            "cohort_metrics", {"cohort": cohort, "window": self._window()}, self.connection
        )
        self.assertEqual(
            response["payment_metrics"]["attempted_payments"], measured["attempted_payments"]
        )
        self.assertEqual(
            response["payment_metrics"]["approved_payments"], measured["approved_payments"]
        )
        self.assertAlmostEqual(
            response["payment_metrics"]["approval_conversion"],
            measured["approval_conversion"],
            places=9,
        )

    def test_financial_impact_agrees_with_the_incident_record(self):
        """Two divergent answers to 'what did it cost' is the failure to prevent."""
        response = evidence.answer(
            "financial_impact", {"incident_id": self.incident["incident_id"]}, self.connection
        )
        recorded = self.incident["financial_impact"]
        self.assertEqual(response["gmv_at_risk"], recorded["gmv_at_risk"])
        self.assertEqual(response["loss_per_hour"], recorded["loss_per_hour"])
        self.assertEqual(response["attempted_value"], recorded["attempted_value"])
        self.assertEqual(
            response["estimated_lost_approved_volume"], recorded["estimated_lost_approved_volume"]
        )

    def test_drilldown_reports_the_cohort_the_incident_names(self):
        response = evidence.answer(
            "drilldown", {"incident_id": self.incident["incident_id"]}, self.connection
        )
        self.assertEqual(response["levels"][-1]["cohort"], self.incident["affected_cohort"])
        self.assertTrue(response["stop_reason"])

    def test_payment_and_attempt_conversion_stay_separate_on_the_wire(self):
        response = evidence.answer(
            "cohort_metrics", {"cohort": COHORT, "window": self._window()}, self.connection
        )
        self.assertNotEqual(
            response["payment_metrics"]["approval_conversion"],
            response["attempt_metrics"]["approval_conversion"],
        )

    def test_retry_stats_does_not_treat_a_retry_as_a_new_payment(self):
        response = evidence.answer(
            "retry_stats", {"cohort": COHORT, "window": self._window()}, self.connection
        )
        self.assertGreater(response["attempts"], response["payments"])
        self.assertGreater(response["retry_amplification_factor"], 1.0)

    def test_incident_history_finds_the_stored_incident(self):
        response = evidence.answer(
            "incident_history", {"merchant_id": "merchant-a"}, self.connection
        )
        self.assertEqual(response["recurrence"]["prior_matching_incidents"], 1)
        self.assertEqual(response["incidents"][0]["incident_id"], self.incident["incident_id"])

    def test_cohort_compare_shows_a_healthy_sibling_beside_the_target(self):
        response = evidence.answer(
            "cohort_compare",
            {"cohort": COHORT, "window": self._window(), "compare_dimensions": ["provider"]},
            self.connection,
        )
        self.assertTrue(response["siblings"])
        target = response["target"]["payment_metrics"]["approval_conversion"]
        best_sibling = max(
            sibling["payment_metrics"]["approval_conversion"] for sibling in response["siblings"]
        )
        self.assertGreater(best_sibling, target)


class MetricSeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = loaded(synthetic.with_provider_incident())

    def test_points_are_ordered_and_bucketed_on_event_time(self):
        response = evidence.answer(
            "metric_series", {"cohort": COHORT, "window": SERIES_WINDOW}, self.connection
        )
        starts = [point["bucket_start"] for point in response["points"]]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(start.endswith(":00Z") for start in starts))
        self.assertEqual(response["bucket_seconds"], config.BUCKET_SECONDS)

    def test_no_bucket_is_reported_ahead_of_the_watermark(self):
        response = evidence.answer(
            "metric_series",
            {"cohort": COHORT, "window": {"start": "2026-08-30T04:00:00Z", "end": "2027-01-01T00:00:00Z"}},
            self.connection,
        )
        self.assertTrue(response["points"])
        self.assertLessEqual(response["points"][-1]["bucket_end"], response["watermark"])

    def test_the_series_matches_the_measurement_the_detector_trends_on(self):
        response = evidence.answer(
            "metric_series", {"cohort": COHORT, "window": SERIES_WINDOW}, self.connection
        )
        start = evidence.schema.parse_timestamp(SERIES_WINDOW["start"]).timestamp()
        series = {
            evidence.schema.iso_utc(point["bucket_start_epoch"]): point["approval_conversion"]
            for point in metrics.timeseries(
                self.connection, COHORT, int(start), evidence.watermark(self.connection)
            )
        }
        for point in response["points"]:
            self.assertAlmostEqual(point["value"], series[point["bucket_start"]], places=9)

    def test_an_unpublished_metric_is_refused_by_name(self):
        with self.assertRaises(evidence.EvidenceError) as raised:
            evidence.answer(
                "metric_series",
                {"cohort": COHORT, "window": SERIES_WINDOW, "metric": "vibes"},
                self.connection,
            )
        self.assertEqual(raised.exception.code, "invalid_input")
        self.assertIn("payment_approval_conversion", raised.exception.message)

    def test_an_attempt_level_metric_is_available_as_its_own_series(self):
        response = evidence.answer(
            "metric_series",
            {"cohort": COHORT, "window": SERIES_WINDOW, "metric": "attempt_approval_conversion"},
            self.connection,
        )
        self.assertEqual(response["metric"], "attempt_approval_conversion")
        self.assertTrue(response["points"])


class DeterminismTests(unittest.TestCase):
    """Replay is the basis of every other claim this layer makes."""

    def test_the_same_events_in_a_different_order_give_the_same_answers(self):
        events = synthetic.with_provider_incident()
        forward = loaded(events)
        backward = loaded(list(reversed(events)))
        detected(forward)
        detected(backward)
        for tool, request in CALLS.items():
            with self.subTest(tool=tool):
                self.assertEqual(
                    evidence.answer(tool, request, forward),
                    evidence.answer(tool, request, backward),
                )

    def test_as_of_is_a_function_of_the_events_and_not_of_the_clock(self):
        connection = loaded(synthetic.healthy())
        first = evidence.answer("cohort_metrics", CALLS["cohort_metrics"], connection)
        second = evidence.answer("cohort_metrics", CALLS["cohort_metrics"], connection)
        self.assertEqual(first["as_of"], second["as_of"])
        self.assertLessEqual(first["as_of"], CALLS["cohort_metrics"]["window"]["end"])


class QueryIdentityTests(unittest.TestCase):
    """Every cited fact in an investigation resolves through this string."""

    def setUp(self):
        sys.path.insert(0, str(TOOL_DIR))
        import _common  # noqa: PLC0415

        self.common = _common

    def test_identical_input_gives_an_identical_query_id(self):
        request = {"cohort": COHORT, "window": WINDOW}
        self.assertEqual(
            self.common.query_id("cohort_metrics", request),
            self.common.query_id("cohort_metrics", dict(reversed(list(request.items())))),
        )

    def test_the_published_algorithm_is_unchanged(self):
        """The value W3 has already cited, recomputed from the contract example."""
        self.assertEqual(
            self.common.query_id(
                "cohort_metrics",
                {
                    "cohort": {
                        "merchant_id": "merchant-a",
                        "provider": "provider-p2",
                        "country": "CO",
                        "card_network": "mastercard",
                    },
                    "window": {
                        "start": "2026-08-29T10:00:00Z",
                        "end": "2026-08-29T10:15:00Z",
                    },
                },
            ),
            "q_cohort_metrics_4c7bf85539781845",
        )

    def test_a_different_input_gives_a_different_query_id(self):
        self.assertNotEqual(
            self.common.query_id("cohort_metrics", {"cohort": COHORT, "window": WINDOW}),
            self.common.query_id("cohort_metrics", {"cohort": {}, "window": WINDOW}),
        )


class WireProtocolTests(unittest.TestCase):
    """One JSON object in, one JSON object out, for a real subprocess call."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / "clearwave.db"
        connection = store.connect(cls.database)
        store.ingest(connection, synthetic.with_provider_incident())
        cls.incident = detected(connection)
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def invoke(self, tool: str, payload: str, database: Path | None = None):
        environment = dict(os.environ)
        environment[store.DB_ENV_VAR] = str(self.database if database is None else database)
        return subprocess.run(
            [sys.executable, str(TOOL_DIR / f"{tool}.py")],
            input=payload,
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=environment,
            timeout=30,
        )

    def test_each_tool_returns_one_object_with_query_id_and_as_of(self):
        for tool, request in CALLS.items():
            with self.subTest(tool=tool):
                completed = self.invoke(tool, json.dumps(request))
                self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
                response = json.loads(completed.stdout)
                self.assertTrue(response["query_id"].startswith(f"q_{tool}_"))
                self.assertTrue(response["as_of"])

    def test_the_tools_read_the_store_the_environment_names(self):
        empty = Path(self.directory.name) / "empty.db"
        populated = json.loads(
            self.invoke("cohort_metrics", json.dumps(CALLS["cohort_metrics"])).stdout
        )
        blank = json.loads(
            self.invoke(
                "cohort_metrics", json.dumps(CALLS["cohort_metrics"]), database=empty
            ).stdout
        )
        self.assertGreater(populated["payment_metrics"]["attempted_payments"], 0)
        self.assertEqual(blank["payment_metrics"]["attempted_payments"], 0)

    def test_malformed_stdin_returns_the_published_error_envelope(self):
        completed = self.invoke("cohort_metrics", "not json at all")
        self.assertEqual(completed.returncode, 1)
        error = json.loads(completed.stdout)["error"]
        self.assertEqual(error["code"], "invalid_json")
        self.assertTrue(error["message"])

    def test_an_unsupported_cohort_dimension_is_refused_with_a_code(self):
        completed = self.invoke(
            "cohort_metrics", json.dumps({"cohort": {"pizza": "yes"}, "window": WINDOW})
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["error"]["code"], "invalid_input")

    def test_external_status_still_answers_from_its_published_fixture(self):
        """W3 owns it. W2 must not have quietly rewired it."""
        completed = self.invoke("external_status", json.dumps({"provider": "provider-p2"}))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["status"], "unavailable")

    def test_a_tool_answers_the_same_way_twice_for_the_same_input(self):
        payload = json.dumps(CALLS["metric_series"])
        self.assertEqual(
            self.invoke("metric_series", payload).stdout,
            self.invoke("metric_series", payload).stdout,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
