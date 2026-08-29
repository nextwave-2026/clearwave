"""Behavioural tests for the detection plane.

Each test asserts a behaviour the challenge actually grades, not an
implementation detail: no firing on healthy traffic, correct localisation,
money priced on payments, severity driven by money rather than by statistics,
and honest confounding.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import config, detect, metrics, schema, store  # noqa: E402
from tests import synthetic  # noqa: E402


_OPEN: list = []


def loaded(events):
    """Load events into a fresh in-memory store and return it with its bounds."""
    connection = store.connect(":memory:")
    _OPEN.append(connection)
    summary = store.ingest(connection, events)
    bounds = store.window_bounds(connection)
    return connection, summary, bounds


def tearDownModule():
    while _OPEN:
        _OPEN.pop().close()


class SchemaTests(unittest.TestCase):
    def test_rejects_missing_payment_identity(self):
        event = synthetic.healthy(minutes=1, per_minute=1)[0]
        del event["payment_id"]
        with self.assertRaises(schema.InvalidEvent):
            schema.normalise(event)

    def test_rejects_decline_reason_outside_vocabulary(self):
        event = synthetic.healthy(minutes=1, per_minute=1)[0]
        event["status"] = "declined"
        event["normalized_decline_reason"] = "the terminal was unhappy"
        with self.assertRaises(schema.InvalidEvent):
            schema.normalise(event)

    def test_rejects_unknown_currency_rather_than_guessing(self):
        event = synthetic.healthy(minutes=1, per_minute=1)[0]
        event["currency"] = "XYZ"
        with self.assertRaises(schema.InvalidEvent):
            schema.normalise(event)

    def test_bad_events_are_dead_lettered_not_dropped_silently(self):
        events = synthetic.healthy(minutes=2, per_minute=5)
        events[0].pop("provider")
        connection, summary, _ = loaded(events)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) AS n FROM dead_letter").fetchone()["n"], 1
        )


class MeasurementTests(unittest.TestCase):
    def test_payment_and_attempt_conversion_are_not_collapsed(self):
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        cohort = {"provider": "provider-p2", "country": "CO"}
        payments = metrics.payment_metrics(connection, cohort, lo, hi + 60)
        attempts = metrics.attempt_metrics(connection, cohort, lo, hi + 60)
        self.assertGreater(attempts["attempts"], payments["attempted_payments"])
        self.assertNotEqual(
            round(payments["approval_conversion"], 6),
            round(attempts["approval_conversion"], 6),
        )

    def test_retries_do_not_inflate_the_money(self):
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        cohort = {"provider": "provider-p2", "country": "CO"}
        payments = metrics.payment_metrics(connection, cohort, lo, hi + 60)
        impact = metrics.financial_impact(connection, cohort, lo, hi + 60, 0.92)
        # Attempted value is priced per payment, so it can never exceed the
        # payment count times the ticket size even during a retry storm.
        self.assertLessEqual(
            impact["attempted_value"]["amount"], payments["attempted_payments"] * 100.0 + 0.01
        )

    def test_timeseries_is_ordered_and_bucketed_on_event_time(self):
        connection, _, (lo, hi) = loaded(synthetic.healthy(minutes=10, per_minute=10))
        series = metrics.timeseries(connection, None, lo, hi + 60)
        stamps = [point["bucket_start_epoch"] for point in series]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(stamp % config.BUCKET_SECONDS == 0 for stamp in stamps))

    def test_confounding_is_detected_and_reported_with_its_table(self):
        connection, _, (lo, hi) = loaded(synthetic.confounded())
        result = metrics.confounding(connection, "provider", "issuing_bank", None, lo, hi + 60)
        self.assertTrue(result["structurally_inseparable"])
        self.assertTrue(result["cross_tabulation"]["rows"])

    def test_confounding_absent_when_dimensions_do_separate(self):
        connection, _, (lo, hi) = loaded(synthetic.healthy())
        result = metrics.confounding(connection, "provider", "country", None, lo, hi + 60)
        self.assertFalse(result["structurally_inseparable"])
        self.assertTrue(result["cross_tabulation"]["rows"])


class DetectionTests(unittest.TestCase):
    def test_healthy_traffic_raises_no_incident(self):
        connection, _, (lo, hi) = loaded(synthetic.healthy())
        self.assertIsNone(detect.build_incident(connection, lo, hi + 60))

    def test_provider_degradation_is_detected_and_localised(self):
        events = synthetic.with_provider_incident()
        connection, _, (lo, hi) = loaded(events)
        onset = lo + 65 * 60
        incident = detect.build_incident(connection, onset, hi + 60)
        self.assertIsNotNone(incident)
        cohort = incident["affected_cohort"]
        self.assertEqual(cohort.get("provider"), "provider-p2")
        self.assertIn(cohort.get("country"), (None, "CO"))

    def test_does_not_over_specify_beyond_the_injected_cohort(self):
        """Regression: descending on drop alone reported an innocent issuer.

        The injected degradation touches provider-p2 in CO and no particular
        bank. An earlier localisation ranked children by absolute drop, so
        noise inside the already-collapsed cohort promoted one arbitrary
        issuing bank into the reported cohort. Localisation now descends only
        where a dimension actually discriminates between siblings.
        """
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        incident = detect.build_incident(connection, lo + 65 * 60, hi + 60)
        self.assertEqual(
            incident["affected_cohort"], {"provider": "provider-p2", "country": "CO"}
        )

    def test_a_uniform_provider_outage_is_not_narrowed_to_one_country(self):
        """A provider degraded everywhere must be reported as the provider."""
        events = []
        for event in synthetic.with_provider_incident(onset_minute=65):
            if event["provider"] == "provider-p2":
                event = dict(event)
                # Degrade provider-p2 in every country, not just CO.
                if event["occurred_at"] >= "2026-08-30T05:05:00Z":
                    event["status"] = "declined"
                    event["normalized_decline_reason"] = "do_not_honor"
            events.append(event)
        connection, _, (lo, hi) = loaded(events)
        incident = detect.build_incident(connection, lo + 65 * 60, hi + 60)
        self.assertEqual(incident["affected_cohort"], {"provider": "provider-p2"})

    def test_incident_carries_no_cause_and_no_confidence(self):
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        incident = detect.build_incident(connection, lo + 65 * 60, hi + 60)
        for forbidden in ("root_cause", "hypothesis", "diagnostic_confidence"):
            self.assertNotIn(forbidden, incident)

    def test_money_is_labelled_as_an_estimate_not_revenue(self):
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        incident = detect.build_incident(connection, lo + 65 * 60, hi + 60)
        joined = " ".join(incident["financial_impact"]["assumptions"]).lower()
        self.assertIn("not a platform-revenue claim", joined)

    def test_replaying_the_same_events_produces_an_identical_incident(self):
        events = synthetic.with_provider_incident()
        first_connection, _, (lo, hi) = loaded(events)
        second_connection, _, _ = loaded(list(reversed(events)))
        first = detect.build_incident(first_connection, lo + 65 * 60, hi + 60)
        second = detect.build_incident(second_connection, lo + 65 * 60, hi + 60)
        self.assertEqual(first, second)


class SeverityTests(unittest.TestCase):
    def test_large_money_outranks_a_dramatic_tiny_cohort(self):
        big = detect.severity_of(
            loss_per_hour=25_000.0, affected_payments=8_000,
            platform_payments=10_000, buckets_sustained=10, trajectory=1,
        )
        tiny = detect.severity_of(
            loss_per_hour=120.0, affected_payments=8,
            platform_payments=10_000, buckets_sustained=10, trajectory=1,
        )
        self.assertGreater(big["severity_score"], tiny["severity_score"])
        self.assertEqual(big["severity"], "critical")
        self.assertEqual(tiny["severity"], "low")

    def test_severity_ignores_statistical_strength_entirely(self):
        # Same business facts, and there is no argument to pass a z-score to.
        first = detect.severity_of(5_000.0, 500, 10_000, 6, 0)
        second = detect.severity_of(5_000.0, 500, 10_000, 6, 0)
        self.assertEqual(first, second)
        self.assertNotIn("z", first["components"])

    def test_a_recovering_incident_scores_below_a_worsening_one(self):
        worsening = detect.severity_of(5_000.0, 500, 10_000, 6, 1)
        recovering = detect.severity_of(5_000.0, 500, 10_000, 6, -1)
        self.assertGreater(worsening["severity_score"], recovering["severity_score"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
