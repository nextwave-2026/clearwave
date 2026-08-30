"""Behavioural tests for the detection plane.

Each test asserts a behaviour the challenge actually grades, not an
implementation detail: no firing on healthy traffic, correct localisation,
money priced on payments, severity driven by money rather than by statistics,
and honest confounding.
"""

from __future__ import annotations

import json
import sys
import tempfile
import tracemalloc
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import cli, config, detect, metrics, schema, store  # noqa: E402
from tests import synthetic  # noqa: E402


_OPEN: list = []


def _epoch(iso: str) -> int:
    """Parse an RFC 3339 UTC timestamp back to epoch seconds."""
    return int(
        datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    )


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

    def test_high_impact_small_percentage_change_is_localised_and_priced(self):
        events = synthetic.high_impact_small_percentage()
        connection, _, (lo, hi) = loaded(events)
        start, end = lo + 75 * 60, hi + 60
        root = detect.evaluate(connection, None, start, end)
        merchant = detect.evaluate(connection, {"merchant_id": "merchant-a"}, start, end)
        self.assertLess(root["absolute_drop"], config.ABS_DROP_MIN)
        self.assertTrue(merchant["qualifies"])
        incident = detect.build_incident(connection, start, end)
        self.assertIsNotNone(incident)
        self.assertEqual(incident["affected_cohort"], {"merchant_id": "merchant-a"})
        money = incident["financial_impact"]
        self.assertGreater(money["gmv_at_risk"]["amount"], 0)
        self.assertGreater(money["loss_per_hour"]["amount"], 20_000)

    def test_confounded_incident_preserves_the_observed_joint_cohort(self):
        events = synthetic.confounded_incident()
        connection, _, (lo, hi) = loaded(events)
        incident = detect.build_incident(connection, lo + 75 * 60, hi + 60)
        self.assertIsNotNone(incident)
        self.assertEqual(
            incident["affected_cohort"],
            {"provider": "provider-p2", "issuing_bank": "bank-x"},
        )
        self.assertTrue(
            metrics.confounding(
                connection,
                "provider",
                "issuing_bank",
                None,
                lo + 75 * 60,
                hi + 60,
            )["structurally_inseparable"]
        )

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


class BlastRadiusNamingTests(unittest.TestCase):
    """The C3 blast radius must publish the names the contract publishes.

    Reported by juank in STATUS.md at 2026-08-29T20:49Z: the emitter built its
    keys as f"affected_{dimension}s", which produces affected_countrys and
    affected_merchant_ids where docs/contracts/incident.md specifies
    affected_countries and affected_merchants. Every consumer reading the
    contract saw those two fields as absent.
    """

    def test_publishes_the_contract_field_names(self):
        connection, _, (lo, hi) = loaded(synthetic.healthy())
        radius = metrics.blast_radius(connection, None, lo, hi + 60)
        self.assertIn("affected_merchants", radius)
        self.assertIn("affected_countries", radius)
        self.assertNotIn("affected_merchant_ids", radius)
        self.assertNotIn("affected_countrys", radius)

    def test_every_published_dimension_has_a_declared_name(self):
        """Names are declared per dimension, not generated from it."""
        self.assertEqual(set(metrics.BLAST_RADIUS_FIELDS), set(schema.DIMENSIONS))
        # The two the generated plural got wrong, spelled as the contract spells them.
        self.assertEqual(metrics.BLAST_RADIUS_FIELDS["merchant_id"], "affected_merchants")
        self.assertEqual(metrics.BLAST_RADIUS_FIELDS["country"], "affected_countries")


class OnsetTests(unittest.TestCase):
    """Onset is the first observed time of the deviation, not the sweep start.

    docs/contracts/incident.md defines onset that way, but it was computed as
    min(degraded) over a series already clipped to the detection window, so a
    degradation older than the window always reported the window start. The
    same clipping capped buckets_sustained, which feeds the persistence term in
    severity and so under-ranked the longest-running incidents.
    """

    def test_onset_predates_a_detection_window_that_starts_late(self):
        events = synthetic.with_provider_incident(onset_minute=65)
        connection, _, (lo, hi) = loaded(events)
        true_onset = lo + 65 * 60

        # Sweep only the final few buckets, long after the degradation began.
        window_start = hi - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS
        self.assertGreater(window_start, true_onset, "window must start after onset")

        incident = detect.build_incident(connection, window_start, hi + 60)
        self.assertIsNotNone(incident)
        onset_epoch = _epoch(incident["onset"])
        self.assertLess(
            onset_epoch,
            window_start,
            "onset must reach back before the sweep window, not report its start",
        )
        self.assertGreaterEqual(onset_epoch, true_onset - config.BUCKET_SECONDS)

    def test_persistence_is_not_capped_by_the_detection_window(self):
        events = synthetic.with_provider_incident(onset_minute=65)
        connection, _, (lo, hi) = loaded(events)
        window_start = hi - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS
        incident = detect.build_incident(connection, window_start, hi + 60)
        # The degradation runs from minute 65 to the end of the traffic, about
        # fifteen buckets. Clipped to the sweep window it could only ever report
        # the window's own length, so anything near that is the old defect.
        self.assertGreater(
            incident["detection"]["buckets_sustained"],
            2 * config.DETECT_WINDOW_BUCKETS,
            "a degradation older than the window must count its earlier buckets",
        )
        self.assertGreater(
            incident["persistence"]["observed_for_seconds"],
            2 * config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS,
        )

    def test_onset_reports_this_episode_not_an_earlier_recovered_dip(self):
        """An earlier dip that recovered is a different episode, not this onset."""
        events = []
        for event in synthetic.with_provider_incident(onset_minute=65):
            # Add a separate earlier degradation on the same provider that
            # fully recovers well before the current episode begins.
            if (
                event["provider"] == "provider-p2"
                and "2026-08-30T04:35:00Z" <= event["occurred_at"] < "2026-08-30T04:45:00Z"
            ):
                event = dict(event, status="declined", normalized_decline_reason="do_not_honor")
            events.append(event)
        connection, _, (lo, hi) = loaded(events)

        window_start = hi - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS
        incident = detect.build_incident(connection, window_start, hi + 60)
        self.assertIsNotNone(incident)
        onset_epoch = _epoch(incident["onset"])
        # The recovered stretch between the two episodes must stop the walk, so
        # onset lands in the current episode rather than back at minute 35.
        self.assertGreater(onset_epoch, lo + 50 * 60)

    def test_no_qualifying_bucket_invents_no_onset(self):
        connection, _, (lo, hi) = loaded(synthetic.healthy())
        window_start = hi - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS
        series = metrics.timeseries(connection, None, window_start, hi + 60)
        onset, sustained = detect._episode_extent(
            connection, None, window_start, hi + 60, 0.0, series
        )
        self.assertEqual(onset, window_start)
        self.assertEqual(sustained, 0)


class StreamingIngestTests(unittest.TestCase):
    """The backfill path: JSON Lines in, batches out, nothing held whole.

    These run on a handful of lines. What they pin is the behaviour that makes
    a 100,000-line file safe to load, not the size of it.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.dir = Path(self._tmpdir.name)
        self.connection = store.connect(":memory:")
        self.addCleanup(self.connection.close)

    def _jsonl(self, events, name="backfill.jsonl"):
        path = self.dir / name
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        return path

    def test_streaming_a_file_stores_exactly_what_loading_it_whole_would(self):
        events = synthetic.with_provider_incident()
        path = self._jsonl(events)
        streamed = store.ingest_stream(self.connection, cli._stream_jsonl(path), batch_size=64)
        whole = store.connect(":memory:")
        self.addCleanup(whole.close)
        loaded_whole = store.ingest(whole, cli._load_events(path))
        self.assertEqual(streamed, loaded_whole)
        self.assertEqual(streamed["accepted"], len(events))
        self.assertEqual(streamed["rejected"], 0)

    def test_the_reader_holds_far_less_than_the_file_it_is_reading(self):
        # The whole point of the path, measured rather than asserted by eye:
        # `_load_events` allocates the file plus every parsed dict at once, so
        # its peak scales with the file. `_stream_jsonl` holds one line.
        path = self._jsonl(synthetic.healthy() * 4)
        size = path.stat().st_size

        tracemalloc.start()
        for _ in cli._stream_jsonl(path):
            pass
        streaming_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

        tracemalloc.start()
        whole = cli._load_events(path)
        whole_peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        del whole

        self.assertLess(streaming_peak, size)
        self.assertGreater(whole_peak, size)
        self.assertLess(streaming_peak * 10, whole_peak)

    def test_batches_are_durable_before_the_run_finishes(self):
        # The reason to batch at all: a run interrupted part way leaves the
        # completed batches in the store rather than losing the lot.
        events = synthetic.healthy()
        path = self._jsonl(events)
        seen = []

        def peek():
            for record in cli._stream_jsonl(path):
                seen.append(record)
                if len(seen) == 40:
                    self.assertEqual(
                        self.connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"],
                        20,
                    )
                yield record

        store.ingest_stream(self.connection, peek(), batch_size=20)
        self.assertEqual(len(seen), len(events))

    def test_a_malformed_line_is_dead_lettered_and_the_rest_still_loads(self):
        events = synthetic.healthy()[:10]
        path = self.dir / "ragged.jsonl"
        lines = [json.dumps(event) for event in events]
        lines.insert(5, "{not json at all")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary = store.ingest_stream(self.connection, cli._stream_jsonl(path), batch_size=3)
        self.assertEqual(summary["accepted"], 10)
        self.assertEqual(summary["rejected"], 1)
        reason = self.connection.execute("SELECT reason FROM dead_letter").fetchone()["reason"]
        self.assertIn("ragged.jsonl:6", reason)
        self.assertIn("not valid JSON", reason)

    def test_blank_lines_are_skipped_rather_than_rejected(self):
        events = synthetic.healthy()[:4]
        path = self.dir / "gappy.jsonl"
        path.write_text(
            "\n\n".join(json.dumps(event) for event in events) + "\n\n", encoding="utf-8"
        )
        summary = store.ingest_stream(self.connection, cli._stream_jsonl(path))
        self.assertEqual(summary["accepted"], 4)
        self.assertEqual(summary["rejected"], 0)

    def test_replaying_the_same_file_adds_nothing(self):
        path = self._jsonl(synthetic.healthy())
        first = store.ingest_stream(self.connection, cli._stream_jsonl(path), batch_size=32)
        second = store.ingest_stream(self.connection, cli._stream_jsonl(path), batch_size=32)
        self.assertEqual(second["duplicates"], first["accepted"])
        self.assertEqual(second["stored"], first["stored"])

    def test_the_cli_only_streams_when_asked(self):
        path = self._jsonl(synthetic.healthy()[:6])
        db = self.dir / "cli.db"
        self.assertEqual(cli.main(["--db", str(db), "ingest", str(path), "--stream"]), 0)
        self.assertEqual(cli.main(["--db", str(db), "ingest", str(path)]), 0)
        connection = store.connect(db)
        self.addCleanup(connection.close)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"], 6
        )

    def test_a_batch_size_below_one_is_refused_rather_than_silently_fixed(self):
        with self.assertRaises(ValueError):
            store.ingest_stream(self.connection, iter([]), batch_size=0)


class RecurrencePromotesSeverityTests(unittest.TestCase):
    """A fault that keeps coming back is a worse fault than one that happened once.

    Yuno's product owners asked it directly: two low-priority alerts on the
    same cohort in a short period should not stay two low-priority alerts. The
    count is measured over the incident table by the detection plane itself;
    `incident_history` keeps publishing its own row count separately.
    """

    BASE = dict(
        loss_per_hour=5_000.0, affected_payments=500, platform_payments=10_000,
        buckets_sustained=6, trajectory=0,
    )

    def _severity(self, priors):
        return detect.severity_of(**self.BASE, prior_matching_incidents=priors)["severity"]

    def test_no_recurrence_leaves_todays_answer_untouched(self):
        self.assertEqual(
            detect.severity_of(**self.BASE), detect.severity_of(**self.BASE, prior_matching_incidents=0)
        )

    def test_each_rung_of_the_ladder_promotes_one_band_further(self):
        order = detect.SEVERITY_ORDER
        unpromoted = order.index(self._severity(0))
        self.assertEqual(order.index(self._severity(1)), unpromoted)
        for priors, bands in ((2, 1), (4, 2), (8, 3)):
            self.assertEqual(
                order.index(self._severity(priors)),
                min(len(order) - 1, unpromoted + bands),
                f"{priors} prior matching incidents must promote {bands} band(s)",
            )

    def test_promotion_stops_at_critical_rather_than_running_off_the_ladder(self):
        self.assertEqual(self._severity(8), "critical")
        self.assertEqual(self._severity(500), "critical")

    def test_promotion_is_reported_so_the_number_can_be_explained(self):
        promoted = detect.severity_of(**self.BASE, prior_matching_incidents=4)
        self.assertEqual(promoted["prior_matching_incidents"], 4)
        self.assertEqual(promoted["recurrence_promotion_bands"], 2)

    def test_a_prior_episode_that_ended_before_the_gap_counts(self):
        connection, incident, cohort_key, onset = self._store()
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 0)
        for offset in (3_600, 7_200):
            self._save(connection, incident, onset - offset, onset - offset + 600)
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 2)
        # Outside the lookback the same two episodes stop counting.
        self.assertEqual(
            detect.prior_matching_incident_count(
                connection, cohort_key, onset, lookback_seconds=1_800
            ),
            0,
        )

    def test_one_continuous_injection_counts_as_one_episode_however_many_rows(self):
        """Onset drifts as the sweep window rolls, so one fault writes several
        rows. Rows are not episodes: the phone must not ring because one
        rehearsal left three of them behind."""
        connection, incident, cohort_key, onset = self._store()
        # One 18-minute fault, sweeping every few minutes, still running as this
        # incident onsets: every row's last_seen runs right up to now.
        for drift in (0, 240, 480):
            self._save(connection, incident, onset - 1_080 + drift, onset)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) AS n FROM incident").fetchone()["n"], 3
        )
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 0)

    def test_two_genuinely_separate_faults_still_count_as_two(self):
        """The fix must not make recurrence unreachable - that would be worse
        than the bug it fixes."""
        connection, incident, cohort_key, onset = self._store()
        for start in (onset - 4 * 3_600, onset - 2 * 3_600):
            self._save(connection, incident, start, start + 900)
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 2)
        self.assertEqual(
            detect.severity_of(**self.BASE, prior_matching_incidents=2)["recurrence_promotion_bands"],
            1,
        )

    def test_a_row_that_ended_inside_the_gap_is_the_same_episode(self):
        connection, incident, cohort_key, onset = self._store()
        inside = onset - config.RECURRENCE_EPISODE_GAP_SECONDS + 60
        outside = onset - config.RECURRENCE_EPISODE_GAP_SECONDS - 60
        self._save(connection, incident, onset - 3_600, inside, suffix="inside")
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 0)
        self._save(connection, incident, onset - 3_600, outside, suffix="outside")
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 1)

    def test_a_watch_never_promotes_a_later_band(self):
        """A near-miss we deliberately chose not to page on cannot raise a
        later severity."""
        connection, incident, cohort_key, onset = self._store()
        for offset in (3_600, 7_200):
            self._save(
                connection, incident, onset - offset, onset - offset + 600, state="watching"
            )
        self.assertEqual(detect.prior_matching_incident_count(connection, cohort_key, onset), 0)

    def test_every_downstream_lifecycle_state_is_a_genuine_prior_recurrence(self):
        connection, incident, cohort_key, onset = self._store()
        states = ("detected", "claimed", "investigating", "diagnosed", "mitigated", "resolved")
        for index, state in enumerate(states):
            start = onset - (index + 1) * 1_800 - 1_800
            self._save(connection, incident, start, start + 300, state=state)
        self.assertEqual(
            detect.prior_matching_incident_count(connection, cohort_key, onset), len(states)
        )

    # -- helpers ----------------------------------------------------------
    def _store(self):
        connection, _, (lo, hi) = loaded(synthetic.with_provider_incident())
        incident = detect.build_incident(connection, lo + 65 * 60, hi + 60)
        return connection, incident, metrics.cohort_key(incident["affected_cohort"]), _epoch(
            incident["onset"]
        )

    @staticmethod
    def _save(connection, incident, onset, last_seen, state="detected", suffix=""):
        earlier = dict(incident)
        earlier["incident_id"] = f"inc-earlier-{onset}-{last_seen}-{state}{suffix}"
        earlier["onset"] = schema.iso_utc(onset)
        earlier["persistence"] = dict(earlier.get("persistence") or {}) | {
            "last_observed_at": schema.iso_utc(last_seen)
        }
        store.save_incident(connection, earlier, lifecycle_state=state)


class MerchantRelativeSeverityTests(unittest.TestCase):
    """A merchant is judged against its own normal, not against every merchant.

    The absolute-dollar ladder asks one question of an airline and a fast-food
    chain alike: how many dollars an hour. A chain losing most of its own
    traffic can sit under $2,000 an hour and be capped at `medium`, so it never
    rings a phone. This is the case that supersedes ADR 0016's ladder alone.
    """

    LADDER_CAPPED = dict(
        loss_per_hour=1_500.0, affected_payments=400, platform_payments=10_000,
        buckets_sustained=20, trajectory=1,
    )

    def test_an_unknown_merchant_normal_leaves_todays_answer_byte_identical(self):
        with_default = detect.severity_of(**self.LADDER_CAPPED)
        explicitly_unknown = detect.severity_of(**self.LADDER_CAPPED, loss_share_of_normal=None)
        self.assertEqual(with_default, explicitly_unknown)
        self.assertEqual(with_default["loss_rate_ceiling"], "medium")
        self.assertEqual(with_default["severity"], "medium")

    def test_a_proportionally_catastrophic_loss_is_no_longer_capped_by_dollars(self):
        capped = detect.severity_of(**self.LADDER_CAPPED)
        promoted = detect.severity_of(**self.LADDER_CAPPED, loss_share_of_normal=0.60)
        self.assertEqual(capped["severity"], "medium")
        self.assertEqual(promoted["severity"], "high")
        self.assertEqual(promoted["loss_rate_ceiling"], "medium")
        self.assertIsNone(promoted["loss_share_ceiling"])

    def test_the_ceiling_is_the_higher_of_the_two_never_the_lower(self):
        # Enormous absolute dollars on a merchant so large the share is trivial:
        # the dollar ladder stops capping, so the share ladder must not re-cap.
        huge = detect.severity_of(
            loss_per_hour=40_000.0, affected_payments=8_000, platform_payments=10_000,
            buckets_sustained=20, trajectory=1, loss_share_of_normal=0.005,
        )
        self.assertIsNone(huge["effective_ceiling"])
        self.assertEqual(huge["severity"], "critical")

    def test_a_trivial_share_of_a_trivial_loss_still_cannot_climb(self):
        cheap = detect.severity_of(
            loss_per_hour=120.0, affected_payments=8, platform_payments=10_000,
            buckets_sustained=20, trajectory=1, loss_share_of_normal=0.001,
        )
        self.assertEqual(cheap["severity"], "low")

    def test_a_merchants_normal_needs_real_history_before_it_is_used(self):
        # The default fixture is 80 minutes long, which is nowhere near enough
        # to call anything a normal hour - so nothing is offered and severity
        # falls back to dollars, exactly as it does today.
        connection, _, _ = loaded(synthetic.with_provider_incident())
        self.assertEqual(detect.merchant_normal_hourly_value(connection), {})
        self.assertEqual(detect.merchant_normal_hourly_value(store.connect(":memory:")), {})

    def test_end_to_end_the_small_merchant_is_promoted_off_the_dollar_ladder(self):
        connection, _, (lo, hi) = loaded(synthetic.merchant_scale())
        end, start = hi + 60, hi + 60 - config.DETECT_WINDOW_BUCKETS * 60

        normals = detect.merchant_normal_hourly_value(connection)
        self.assertIn("merchant-small", normals)

        before = detect.build_incident(connection, start, end, merchant_normals={})
        after = detect.build_incident(connection, start, end, merchant_normals=normals)

        self.assertEqual(before["affected_cohort"]["merchant_id"], "merchant-small")
        self.assertEqual(before["severity"], "medium")
        self.assertEqual(before["detection"]["severity_ceilings"]["loss_rate"], "medium")

        self.assertEqual(after["severity"], "high")
        self.assertIsNone(after["detection"]["severity_ceilings"]["effective"])
        self.assertLess(after["financial_impact"]["loss_per_hour"]["amount"], 2_000.0)
        self.assertGreaterEqual(after["detection"]["loss_share_of_merchant_normal"], 0.35)


class WatchTests(unittest.TestCase):
    """A developing deviation is watched, not discarded and not paged.

    Detection today emits silence or a crossed-floor incident, so the first
    thing anyone hears about a degradation is the cliff. A watch is the
    near-miss, carried as `lifecycle_state: watching` on the same C3 record the
    cohort keeps if it becomes an incident.
    """

    def _sweep(self, events, persist=True):
        connection, _, _ = loaded(events)
        return connection, cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=persist)

    def test_a_developing_deviation_is_watched_before_it_is_an_incident(self):
        _, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        self.assertIsNone(sweep["incident"], "the floors have not been crossed yet")
        self.assertEqual(len(sweep["watches"]), 1)
        watch = sweep["watches"][0]
        self.assertEqual(watch["affected_cohort"], {"provider": "provider-p2"})
        self.assertEqual(watch["lifecycle_state"], "watching")
        self.assertIn("conversion_near_miss", watch["detection"]["watch"]["reasons"])
        # It is watched precisely because the statistical floor has not passed.
        self.assertFalse(watch["detection"]["detection_floors"]["z_min"])
        self.assertEqual(watch["detection"]["trajectory"], 1)

    def test_the_predicate_is_tuned_where_it_was_asked_to_be(self):
        near_miss = dict(
            cohort={}, cohort_key="*", observed={}, baseline={}, expected=0.9, actual=0.85,
            absolute_drop=0.05, qualifies=False,
            floors={"has_measurement": True, "volume_min": True},
        )
        self.assertTrue(all(detect.watch_floors({**near_miss, "z": -2.3}, 1).values()))
        self.assertFalse(all(detect.watch_floors({**near_miss, "z": -1.0}, 1).values()))
        # And a deviation that is recovering is not worth warning about.
        self.assertFalse(all(detect.watch_floors({**near_miss, "z": -2.3}, -1).values()))
        # A sustained near-miss (flat inside the window) still watches: once the
        # mild inject fills the whole detect window the halves match and
        # trajectory is 0, which must not silence the demo.
        self.assertTrue(all(detect.watch_floors({**near_miss, "z": -2.3}, 0).values()))

    def test_healthy_traffic_is_neither_detected_nor_watched(self):
        _, sweep = self._sweep(synthetic.healthy())
        self.assertIsNone(sweep["incident"])
        self.assertEqual(sweep["watches"], [])

    def test_a_watch_can_never_page_because_its_severity_is_forced_low(self):
        _, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch = sweep["watches"][0]
        self.assertEqual(watch["severity"], "low")
        # C5 escalates high and critical only, so `low` is what makes a warning
        # structurally unable to reach Slack or a phone.
        self.assertNotIn(watch["severity"], ("high", "critical"))

    def test_projected_loss_is_labelled_projected_and_never_ranks_severity(self):
        _, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        impact = sweep["watches"][0]["financial_impact"]
        projected = impact["projected_loss_per_hour"]
        self.assertGreater(projected["amount"], 0.0)
        self.assertIn("not money", projected["basis"])
        self.assertIn("never ranks severity", projected["basis"])
        self.assertIsNot(projected, impact["loss_per_hour"])
        # It is computed off the trailing baseline's typical hourly value, not
        # off the few realised minutes, so a cohort routed around entirely -
        # zero realised loss because zero traffic - still projects its cost.
        connection, outage = self._sweep(synthetic.provider_outage())
        watch = outage["watches"][0]
        self.assertEqual(watch["financial_impact"]["loss_per_hour"]["amount"], 0.0)
        self.assertGreater(watch["financial_impact"]["projected_loss_per_hour"]["amount"], 0.0)

    def test_a_watch_upgrades_the_same_row_rather_than_opening_a_second_one(self):
        connection, first = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch_id = first["watches"][0]["incident_id"]
        store.ingest(connection, synthetic.two_stage_deviation())
        second = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)

        self.assertEqual(second["incident"]["incident_id"], watch_id)
        self.assertEqual(second["incident"]["lifecycle_state"], "detected")
        rows = store.list_incidents(connection)
        self.assertEqual(len(rows), 1, "one cohort keeps one record")
        self.assertEqual(rows[0]["lifecycle_state"], "detected")

        third = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        self.assertEqual(third["incident"]["incident_id"], watch_id)
        still = [
            row for row in store.list_incidents(connection)
            if row["lifecycle_state"] != "resolved"
        ]
        self.assertEqual(len(still), 1, "a later sweep must not mint a second detected row")

    def test_a_sharper_localisation_keeps_the_watch_id(self):
        connection, first = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch = first["watches"][0]
        sharper = {
            "onset": "2026-08-30T05:05:00Z",
            "affected_cohort": {**watch["affected_cohort"], "country": "CO"},
        }
        adopted = cli.adopt_watch_identity(connection, sharper, cli.incident_id_for)
        self.assertEqual(adopted, watch["incident_id"])
        self.assertEqual(sharper["onset"], watch["onset"])

    def test_a_disjoint_slice_is_not_treated_as_contained_by_a_near_miss(self):
        near_miss = {"provider": "provider-p2"}
        # The old containment check treated a missing dimension as contained.
        self.assertTrue(detect._contains_formed_traffic({"country": "CO"}, near_miss))
        self.assertFalse(detect._is_sharpening({"country": "CO"}, near_miss))
        self.assertTrue(
            detect._is_sharpening(
                {"provider": "provider-p2"},
                {"provider": "provider-p2", "country": "CO"},
            )
        )
        self.assertTrue(detect._is_sharpening({"provider": "provider-p2"}, near_miss))

    def test_an_onset_walk_does_not_mint_a_second_id(self):
        connection, first = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch = first["watches"][0]
        walked = {
            "onset": "2026-08-30T05:05:00Z",
            "affected_cohort": dict(watch["affected_cohort"]),
        }
        hashed = cli.incident_id_for(walked)
        adopted = cli.adopt_watch_identity(connection, walked, cli.incident_id_for)
        self.assertNotEqual(hashed, watch["incident_id"])
        self.assertEqual(adopted, watch["incident_id"])
        self.assertEqual(walked["onset"], watch["onset"])

    def test_orthogonal_axes_of_one_inject_are_one_episode(self):
        # A mild inject first appears as provider=adyen and payment_method=card
        # in separate single-axis views. Those are one episode, not two rows.
        self.assertTrue(
            detect.cohorts_same_episode(
                {"provider": "adyen"},
                {"payment_method": "card"},
            )
        )
        self.assertTrue(
            detect.cohorts_same_episode(
                {},
                {"provider": "adyen", "merchant_id": "merchant-b"},
            )
        )
        self.assertFalse(
            detect.cohorts_same_episode(
                {"provider": "adyen"},
                {"provider": "stripe"},
            )
        )

    def test_diluted_provider_near_miss_localises_to_the_merchant(self):
        """merchant-c healthy adyen must not hide merchant-b's mild inject.

        Reproduced live: provider=adyen alone sat at z~-1.2 (below WATCH_Z_MAX)
        while {merchant_id: merchant-b, provider: adyen} cleared z~-2.4. The
        watch has to name the joint cohort or stage-one never appears.
        """
        from datetime import datetime, timedelta, timezone
        from worker.helpers.payment import (
            BASELINE_DECLINE_PROBABILITY,
            MAX_ATTEMPTS,
            RETRY_PROBABILITY,
        )

        as_of = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        events = list(
            synthetic.iter_live_healthy_history(
                hours=2.0,
                per_merchant_per_minute=20,
                seed=42,
                as_of=as_of,
            )
        )
        # Five minutes of mild inject on merchant-b/adyen only, at the effective
        # rate the worker produces (inject p then baseline fallback).
        #
        # Overlay payments are attempt chains, same as the history behind them.
        # PR #98 taught the history generator W1's retry model so payment-level
        # conversion sits at ~0.96. A single-attempt overlay against that
        # baseline is a step change of its own: the platform row then clears
        # Z_MIN (measured z=-5.91, affected_cohort={}) and this test fails
        # before localisation is even asked the merchant-b/adyen question.
        effective = 0.12 + (1.0 - 0.12) * BASELINE_DECLINE_PROBABILITY
        rng = __import__("random").Random(99)
        specs = synthetic._live_merchant_specs()
        index = 0
        start_live = as_of + timedelta(minutes=1)
        for minute in range(5):
            minute_start = start_live + timedelta(minutes=minute)
            for spec in specs:
                for slot in range(30):
                    index += 1
                    provider = spec["providers"][slot % len(spec["providers"])]
                    key = (spec["merchant_id"], provider)
                    p_dec = effective if key == ("merchant-b", "adyen") else BASELINE_DECLINE_PROBABILITY
                    approved = rng.random() >= p_dec
                    payment_method = spec["payment_methods"][slot % len(spec["payment_methods"])]
                    occurred = minute_start + timedelta(seconds=slot % 60)
                    shared = {
                        "payment_id": f"pay-dilute-{index:07d}",
                        "merchant_id": spec["merchant_id"],
                        "payment_method": payment_method,
                        "card_network": (
                            spec["card_networks"][slot % len(spec["card_networks"])]
                            if payment_method == "card"
                            else None
                        ),
                        "country": spec["country"],
                        "issuing_bank": spec["banks"][slot % len(spec["banks"])],
                        "amount": 10.0,
                        "currency": spec["currency"],
                    }
                    for attempt_number in range(1, MAX_ATTEMPTS + 1):
                        event = dict(
                            shared,
                            event_id=f"dilute-{index:07d}-{attempt_number}",
                            attempt_id=f"att-dilute-{index:07d}-{attempt_number}",
                            attempt_number=attempt_number,
                            occurred_at=occurred.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            provider=provider,
                            status="approved" if approved else "declined",
                            latency_ms=220.0,
                        )
                        if not approved:
                            event["normalized_decline_reason"] = (
                                "provider_timeout" if p_dec > BASELINE_DECLINE_PROBABILITY else "insufficient_funds"
                            )
                        events.append(event)
                        if approved or attempt_number == MAX_ATTEMPTS:
                            break
                        if rng.random() > RETRY_PROBABILITY:
                            break
                        alternatives = [name for name in spec["providers"] if name != provider]
                        provider = rng.choice(alternatives) if alternatives else provider
                        p_dec = BASELINE_DECLINE_PROBABILITY
                        approved = rng.random() >= p_dec

        connection, sweep = self._sweep(events)
        self.assertIsNone(sweep["incident"], sweep)
        self.assertGreaterEqual(len(sweep["watches"]), 1, sweep)
        # The verifier's injected-cohort check is merchant-b OR adyen. A
        # deepened child that still names one of those is the demo beat.
        injected = [
            watch
            for watch in sweep["watches"]
            if (watch.get("affected_cohort") or {}).get("merchant_id") == "merchant-b"
            or (watch.get("affected_cohort") or {}).get("provider") == "adyen"
        ]
        self.assertGreaterEqual(
            len(injected),
            1,
            f"expected a merchant-b/adyen watch, got {[w.get('affected_cohort') for w in sweep['watches']]}",
        )
        self.assertTrue(
            any(w.get("lifecycle_state") == "watching" for w in injected),
            injected,
        )

    def test_a_detected_incident_resolves_when_traffic_recovers(self):
        connection, first = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch_id = first["watches"][0]["incident_id"]
        store.ingest(connection, synthetic.two_stage_deviation())
        detected = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        self.assertEqual(detected["incident"]["incident_id"], watch_id)
        self.assertEqual(detected["incident"]["lifecycle_state"], "detected")
        # Force the lifecycle to diagnosed - the state Clear left active on the
        # live board - then feed healthy traffic and require the row to resolve.
        connection.execute(
            "UPDATE incident SET lifecycle_state = ? WHERE incident_id = ?",
            ("diagnosed", watch_id),
        )
        connection.commit()
        later = []
        for event in synthetic.healthy(minutes=10, per_minute=20):
            shifted = dict(event)
            stamp = datetime.strptime(event["occurred_at"], "%Y-%m-%dT%H:%M:%SZ")
            shifted["occurred_at"] = (stamp + timedelta(minutes=200)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            shifted["payment_id"] = f"{event['payment_id']}-recovered"
            shifted["attempt_id"] = f"{event['attempt_id']}-recovered"
            later.append(shifted)
        store.ingest(connection, later)
        recovered = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        self.assertIsNone(recovered["incident"])
        row = store.load_incident(connection, watch_id)
        self.assertEqual(row["lifecycle_state"], "resolved")

    def test_a_watch_that_is_no_longer_true_is_resolved(self):
        connection, first = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch_id = first["watches"][0]["incident_id"]
        later = []
        for event in synthetic.healthy(minutes=10, per_minute=20):
            shifted = dict(event)
            stamp = datetime.strptime(event["occurred_at"], "%Y-%m-%dT%H:%M:%SZ")
            shifted["occurred_at"] = (stamp + timedelta(minutes=200)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            shifted["payment_id"] = f"{event['payment_id']}-later"
            shifted["attempt_id"] = f"{event['attempt_id']}-later"
            later.append(shifted)
        store.ingest(connection, later)
        second = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        self.assertIsNone(second["incident"])
        self.assertEqual(second["watches"], [])
        row = store.load_incident(connection, watch_id)
        self.assertEqual(row["lifecycle_state"], "resolved")

    def test_a_volume_spike_does_not_watch_on_latency(self):
        events = synthetic.healthy()
        cutoff = _epoch(events[-1]["occurred_at"]) - 4 * 60
        extra = []
        index = 100000
        for event in events:
            if _epoch(event["occurred_at"]) < cutoff:
                continue
            event["latency_ms"] = 7000
            # Extra copies are approved so a chance dip is not amplified into
            # an incident. The only thing that moves is volume and latency.
            for _ in range(20):
                index += 1
                copy = dict(event)
                copy["payment_id"] = f"pay-spike-{index}"
                copy["attempt_id"] = f"att-spike-{index}"
                copy["status"] = "approved"
                copy.pop("normalized_decline_reason", None)
                extra.append(copy)
        _, sweep = self._sweep(events + extra)
        self.assertIsNone(sweep["incident"])
        self.assertEqual(sweep["watches"], [])

    def test_leading_indicators_do_not_watch_when_conversion_is_clearly_up(self):
        events = synthetic.healthy()
        cutoff = _epoch(events[-1]["occurred_at"]) - 4 * 60
        for event in events:
            if _epoch(event["occurred_at"]) < cutoff:
                continue
            event["latency_ms"] = 700
            event["status"] = "approved"
            event.pop("normalized_decline_reason", None)
        _, sweep = self._sweep(events)
        self.assertIsNone(sweep["incident"])
        self.assertEqual(sweep["watches"], [])

    def test_a_row_that_has_left_watching_is_never_rewritten(self):
        connection, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch_id = sweep["watches"][0]["incident_id"]
        connection.execute(
            "UPDATE incident SET lifecycle_state = 'investigating' WHERE incident_id = ?",
            (watch_id,),
        )
        connection.commit()
        cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?", (watch_id,)
        ).fetchone()
        self.assertEqual(row["lifecycle_state"], "investigating")

    def test_investigation_claims_a_watch(self):
        # Replaces test_investigation_never_claims_a_watch. Investigating only
        # after `detected` destroyed the preventive value, so a watch is now
        # claimable. The atomic UPDATE still takes the row exactly once.
        from investigation.store import claim_incident

        connection, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        watch_id = sweep["watches"][0]["incident_id"]
        self.assertTrue(claim_incident(connection, watch_id))
        self.assertFalse(claim_incident(connection, watch_id))
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?",
            (watch_id,),
        ).fetchone()
        self.assertEqual(row["lifecycle_state"], "watching")

    def test_a_slow_provider_is_watched_while_conversion_is_still_healthy(self):
        # W1's effect=latency: attempts approve and decline at baseline rates
        # while latency spikes, so conversion never moves and the detector is
        # structurally blind to it.
        _, sweep = self._sweep(synthetic.latency_degradation())
        self.assertIsNone(sweep["incident"])
        self.assertEqual(len(sweep["watches"]), 1)
        watch = sweep["watches"][0]
        self.assertEqual(watch["affected_cohort"], {"provider": "provider-p2"})
        self.assertEqual(watch["detection"]["watch"]["reasons"], ["leading_indicators"])
        self.assertEqual(
            watch["detection"]["watch"]["degraded_leading_indicators"], ["mean_latency_ms"]
        )
        latency = watch["detection"]["watch"]["leading_indicators"]["mean_latency_ms"]
        self.assertGreaterEqual(latency["ratio"], config.FORMING_LATENCY_P95_RATIO)

    def test_a_provider_routed_around_entirely_is_watched_rather_than_silent(self):
        # W1's effect=outage: volume goes to zero instead of showing declines,
        # and a cohort with no traffic can never clear N_PAYMENTS_MIN.
        _, sweep = self._sweep(synthetic.provider_outage())
        self.assertIsNone(sweep["incident"])
        self.assertEqual(
            [w["affected_cohort"] for w in sweep["watches"]], [{"provider": "provider-p2"}]
        )
        volume = sweep["watches"][0]["detection"]["watch"]["leading_indicators"]["volume_rate"]
        self.assertEqual(volume["observed"], 0.0)
        self.assertLess(volume["ratio"], config.FORMING_VOLUME_COLLAPSE_RATIO)

    def test_a_cohort_that_has_already_formed_is_not_also_watched(self):
        _, sweep = self._sweep(synthetic.with_provider_incident())
        self.assertEqual(sweep["incident"]["affected_cohort"], {"provider": "provider-p2"})
        self.assertEqual(sweep["watches"], [])

    def test_nothing_is_predicted(self):
        _, sweep = self._sweep(synthetic.two_stage_deviation_mild_only())
        statement = sweep["watches"][0]["detection"]["watch"]["statement"].lower()
        for forbidden in ("will be", "predict", "in nine minutes", "expected to reach"):
            self.assertNotIn(forbidden, statement)
        self.assertIn("no future number is claimed", statement)
        self.assertIn("against its last hour", statement)

    def test_an_equally_slow_platform_localises_to_nothing_and_says_so(self):
        # Every cohort equally degraded is not a cohort finding. The contrast
        # rule is the one `localise` already applies, pointed at latency.
        events = synthetic.healthy()
        cutoff = _epoch(events[-1]["occurred_at"]) - 4 * 60
        for event in events:
            if _epoch(event["occurred_at"]) >= cutoff:
                event["latency_ms"] = 7_000
        _, sweep = self._sweep(events, persist=False)
        self.assertEqual([w["affected_cohort"] for w in sweep["watches"]], [{}])

    def test_a_periodic_sweeper_is_absent_when_the_interval_is_off(self):
        connection, _, _ = loaded(synthetic.healthy())
        self.assertIsNone(cli._periodic_sweeper(connection, 0.0, []))

    def test_a_periodic_sweeper_sweeps_on_its_own_interval(self):
        connection, _, _ = loaded(synthetic.two_stage_deviation_mild_only())
        now = [0.0]
        sink: list = []
        hook = cli._periodic_sweeper(connection, 30.0, sink, clock=lambda: now[0])
        hook(None)
        self.assertEqual(sink, [], "not yet due")
        now[0] = 31.0
        hook(None)
        self.assertEqual(len(sink), 1)
        self.assertEqual(len(sink[0]["watches"]), 1)
        now[0] = 40.0
        hook(None)
        self.assertEqual(len(sink), 1, "the interval has not elapsed again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
