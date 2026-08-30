"""Healthy live-vocabulary history for the demo store.

The generator must speak the names the workers actually publish, sit behind
now rather than a hard-coded date, stay quiet under detection, and leave the
merchant-relative floors populated. Existing synthetic fixtures are load-bearing
and must keep returning the same shapes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from detector import config, detect, store  # noqa: E402
import prepare_history as prepare  # noqa: E402
from tests import synthetic  # noqa: E402

AS_OF = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


class ExistingFixturesStayPut(unittest.TestCase):
    def test_healthy_still_uses_the_detector_test_vocabulary(self):
        events = synthetic.healthy()
        self.assertEqual(len(events), 80 * 20)
        self.assertEqual(events[0]["occurred_at"], "2026-08-30T04:00:00Z")
        self.assertEqual(events[0]["merchant_id"], "merchant-a")
        self.assertIn(events[0]["provider"], ("provider-p2", "provider-p3"))
        self.assertEqual(events[0]["currency"], "USD")

    def test_two_stage_fixtures_still_model_provider_p2(self):
        full = synthetic.two_stage_deviation()
        mild = synthetic.two_stage_deviation_mild_only()
        self.assertEqual(len(full), 88 * 100)
        self.assertLess(len(mild), len(full))
        self.assertTrue(any(event["provider"] == "provider-p2" for event in full))


class LiveHealthyHistory(unittest.TestCase):
    def test_uses_the_live_worker_vocabulary(self):
        events = synthetic.live_healthy_history(minutes=2, as_of=AS_OF, seed=7)
        self.assertTrue(events)
        merchants = {event["merchant_id"] for event in events}
        self.assertEqual(merchants, {"merchant-a", "merchant-b", "merchant-c"})
        demo = [
            event
            for event in events
            if event["merchant_id"] == "merchant-b" and event["provider"] == "adyen"
        ]
        self.assertTrue(demo)
        self.assertTrue(all(event["country"] == "CO" for event in demo))
        self.assertTrue(all(event["currency"] == "COP" for event in demo))
        self.assertTrue(
            {event["issuing_bank"] for event in demo}
            <= {"Davivienda", "Bancolombia", "Banco de Bogota"}
        )
        self.assertNotIn("provider-p2", {event["provider"] for event in events})
        self.assertNotIn("bank-x", {event["issuing_bank"] for event in events})

    def test_is_deterministic_for_the_same_anchor_and_seed(self):
        first = synthetic.live_healthy_history(minutes=3, as_of=AS_OF, seed=11)
        second = synthetic.live_healthy_history(minutes=3, as_of=AS_OF, seed=11)
        self.assertEqual(first, second)
        shifted = synthetic.live_healthy_history(
            minutes=3,
            as_of=datetime(2026, 8, 30, 15, 0, 0, tzinfo=timezone.utc),
            seed=11,
        )
        self.assertEqual(len(shifted), len(first))
        self.assertNotEqual(shifted[0]["occurred_at"], first[0]["occurred_at"])

    def test_sits_immediately_behind_the_anchor_not_on_a_hard_coded_date(self):
        events = synthetic.live_healthy_history(minutes=5, as_of=AS_OF, seed=3)
        times = [event["occurred_at"] for event in events]
        self.assertTrue(all(stamp <= "2026-08-30T12:00:59Z" for stamp in times))
        self.assertTrue(any(stamp.startswith("2026-08-30T11:55:") for stamp in times))
        self.assertTrue(any(stamp.startswith("2026-08-30T12:00:") for stamp in times))


class HistoryLooksLikeLiveTraffic(unittest.TestCase):
    """The warm start has to be indistinguishable from the traffic it precedes.

    A baseline is a claim about what normal looks like. Where this history and
    W1's live workers disagree, the detector reads the disagreement as a
    deviation on every cohort at once for the first hour - which is exactly
    what the 2026-08-30T07:45Z verification run measured. These four numbers
    are where they used to disagree, so they are pinned.
    """

    def sample(self):
        from worker.helpers.payment import BASELINE_DECLINE_PROBABILITY

        events = synthetic.live_healthy_history(
            minutes=30, per_merchant_per_minute=60, seed=3, as_of=AS_OF
        )
        chains: dict[str, list[dict]] = {}
        for event in events:
            chains.setdefault(event["payment_id"], []).append(event)
        return events, chains, BASELINE_DECLINE_PROBABILITY

    def test_payments_are_attempt_chains_not_single_attempts(self):
        events, chains, decline = self.sample()
        # W1 retries a declined attempt away from the provider that declined
        # it, so a payment can carry more than one attempt and more than one
        # provider. Without that, payment-level conversion is 1 - p and sits
        # about nine points under live traffic.
        self.assertGreater(len(events), len(chains))
        retried = [chain for chain in chains.values() if len(chain) > 1]
        self.assertTrue(retried)
        multi_provider = [
            chain for chain in retried if len({event["provider"] for event in chain}) > 1
        ]
        self.assertTrue(multi_provider)
        for chain in retried:
            self.assertEqual(
                [event["attempt_number"] for event in chain],
                list(range(1, len(chain) + 1)),
            )
            # Only the provider moves within one payment.
            for field in ("merchant_id", "issuing_bank", "payment_method", "currency"):
                self.assertEqual(len({event[field] for event in chain}), 1)

    def test_payment_conversion_matches_the_live_retry_model(self):
        _, chains, decline = self.sample()
        converted = sum(
            1 for chain in chains.values()
            if any(event["status"] == "approved" for event in chain)
        )
        conversion = converted / len(chains)
        # Measured against live traffic on 2026-08-30: 0.954 over the whole
        # capture, 0.974 over its healthy opening. The old flat generator gave
        # 0.875, and every cohort then read z +4 to +7 against it.
        self.assertGreater(conversion, 1 - decline + 0.05)
        self.assertGreater(conversion, 0.93)
        self.assertLess(conversion, 0.99)

    def test_latency_carries_the_live_error_tail(self):
        events, _, _ = self.sample()
        latencies = [event["latency_ms"] for event in events]
        mean = sum(latencies) / len(latencies)
        # Live traffic measured 352ms. A flat 220 put every cohort's latency
        # ratio at 1.58 against live, over FORMING_LATENCY_P95_RATIO, for the
        # whole first hour after a warm start.
        self.assertGreater(mean, 300)
        self.assertLess(mean, 420)
        self.assertGreater(max(latencies), 2000)
        self.assertLess(min(latencies), 100)

    def test_errors_give_the_baseline_a_timeout_share_to_compare_against(self):
        events, _, _ = self.sample()
        timeouts = sum(
            1 for event in events
            if event.get("normalized_decline_reason") == "provider_timeout"
        )
        share = timeouts / len(events)
        # Live measured 0.00625. A zero baseline would hand every injected
        # timeout its full share as the delta.
        self.assertGreater(share, 0.001)
        self.assertLess(share, 0.02)
        self.assertTrue(any(event["status"] == "error" for event in events))


class PreparedStore(unittest.TestCase):
    def test_clean_start_drops_prior_incidents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clearwave.db"
            connection = store.connect(path)
            store.ingest(connection, synthetic.healthy(minutes=2, per_minute=4))
            connection.execute(
                "INSERT INTO incident (incident_id, created_at, record, cohort_key, "
                "severity, severity_score, lifecycle_state, onset_epoch, last_seen_epoch, "
                "config_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "inc-rehearsal",
                    "2026-08-30T00:00:00Z",
                    "{}",
                    "merchant_id=merchant-b|provider=adyen",
                    "high",
                    0.9,
                    "detected",
                    1,
                    1,
                    "det-v1",
                ),
            )
            connection.commit()
            connection.close()
            result = prepare.prepare(
                path,
                hours=1.0,
                per_merchant_per_minute=8,
                seed=20260830,
                as_of=AS_OF,
                keep=False,
                detect_after=False,
            )
            self.assertEqual(result["warmth"]["incident_rows"], 0)
            self.assertGreater(result["ingest"]["accepted"], 0)

    def test_detection_stays_quiet_and_merchant_normal_populates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clearwave.db"
            result = prepare.prepare(
                path,
                hours=6.25,
                per_merchant_per_minute=synthetic.LIVE_HISTORY_PER_MERCHANT_PER_MINUTE,
                seed=20260830,
                as_of=AS_OF,
                keep=False,
                detect_after=True,
            )
            warmth = result["warmth"]
            detection = result["detection"]
            self.assertEqual(detection["incident"], None)
            self.assertEqual(detection["watches"], [])
            self.assertEqual(warmth["incident_rows"], 0)
            self.assertTrue(warmth["baseline_warm"])
            self.assertTrue(warmth["merchant_warm"])
            self.assertGreaterEqual(
                warmth["cohort_buckets"], config.BASELINE_TRAILING_BUCKETS
            )
            self.assertIsNotNone(warmth["merchant_normal_hourly_value_usd"])
            self.assertGreaterEqual(
                warmth["merchant_hours"], config.MERCHANT_NORMAL_MIN_HOURS
            )
            self.assertGreaterEqual(
                warmth["merchant_payments"], config.MERCHANT_NORMAL_MIN_PAYMENTS
            )
            connection = store.connect(path)
            try:
                normals = detect.merchant_normal_hourly_value(connection)
                self.assertIn("merchant-b", normals)
                ceilings = connection.execute(
                    "SELECT COUNT(*) AS n FROM incident"
                ).fetchone()["n"]
                self.assertEqual(ceilings, 0)
            finally:
                connection.close()

    def test_operator_command_replaces_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clearwave.db"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(ROOT / "scripts" / "prepare_history.py"),
                    "--db",
                    str(path),
                    "--hours",
                    "6.25",
                    "--per-minute",
                    str(synthetic.LIVE_HISTORY_PER_MERCHANT_PER_MINUTE),
                    "--as-of",
                    "2026-08-30T12:00:00Z",
                    "--seed",
                    "20260830",
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("detection baseline warm", completed.stdout)
            self.assertIn("merchant-relative warm", completed.stdout)
            self.assertIn("merchant-b/adyen", completed.stdout)
            self.assertIn("incident=None", completed.stdout)
            self.assertIn("watches=0", completed.stdout)


if __name__ == "__main__":
    unittest.main()
