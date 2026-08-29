"""Offline tests for the W4 surfaces layer."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from investigation.store import insert_incident, persist_result
from surfaces.escalation import escalate, notify_slack
from surfaces.inject import fire_hidden_incident
from surfaces.server import SurfacesApp, make_server
from surfaces.store import connect, list_incidents


ROOT = Path(__file__).resolve().parents[1]


def _incident(incident_id, severity, onset, merchant="merchant-a", **fields):
    record = {
        "incident_id": incident_id,
        "affected_cohort": {
            "merchant_id": merchant,
            "provider": "provider-p2",
            "payment_method": "card",
            "card_network": "mastercard",
            "country": "CO",
            "issuing_bank": "bank-x",
        },
        "change": {
            "metric": "payment_approval_conversion",
            "expected": 0.92,
            "actual": 0.64,
            "absolute_delta": -0.28,
            "relative_change": -0.3043478261,
            "unit": "ratio",
        },
        "onset": onset,
        "persistence": {
            "is_persistent": True,
            "observed_for_seconds": 900,
            "last_observed_at": onset,
        },
        "blast_radius": {
            "attempted_payments": 1000,
            "affected_merchants": 1,
            "affected_countries": 1,
            "affected_card_networks": 1,
            "affected_providers": 1,
        },
        "financial_impact": {
            "attempted_value": {"amount": 100000.0, "currency": "USD"},
            "expected_approval_rate": 0.92,
            "actual_approval_rate": 0.64,
            "estimated_lost_approved_volume": {
                "payments": 280,
                "amount": 28000.0,
                "currency": "USD",
            },
            "gmv_at_risk": {"amount": 28000.0, "currency": "USD"},
            "loss_per_hour": {"amount": 112000.0, "currency": "USD"},
        },
        "severity": severity,
        "lifecycle_state": "detected",
    }
    record.update(fields)
    return record


def _trail_entry(sequence=1):
    return {
        "sequence": sequence,
        "query_id": f"q_cohort_metrics_{sequence}",
        "tool": "cohort_metrics",
        "parameters": {"cohort": {"merchant_id": "merchant-a"}},
        "response": {"query_id": f"q_cohort_metrics_{sequence}", "as_of": "2026-08-29T10:15:00Z"},
        "timestamp": "2026-08-29T10:15:00Z",
        "duration_ms": 3.0,
        "outcome": "success",
        "executed": True,
    }


class SurfacesTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        os.environ.pop("CLEARWAVE_SLACK_WEBHOOK_URL", None)
        os.environ.pop("CLEARWAVE_PHONE_PROVIDER", None)
        self.app = SurfacesApp(self.db)

    def _seed(self, *incidents):
        connection = connect(self.db)
        self.addCleanup(connection.close)
        for incident in incidents:
            insert_incident(connection, incident, lifecycle_state=incident.get("lifecycle_state", "detected"))
        return connection

    def test_queue_orders_by_business_priority_not_recency(self):
        self._seed(
            _incident("inc-recent-low", "low", "2026-08-29T12:00:00Z"),
            _incident("inc-old-critical", "critical", "2026-08-29T08:00:00Z"),
            _incident("inc-mid-high", "high", "2026-08-29T11:00:00Z"),
        )
        ordered = [item["incident_id"] for item in list_incidents(connect(self.db))]
        self.assertEqual(ordered, ["inc-old-critical", "inc-mid-high", "inc-recent-low"])
        api_order = [item["incident_id"] for item in self.app.queue()["incidents"]]
        self.assertEqual(api_order, ["inc-old-critical", "inc-mid-high", "inc-recent-low"])
        recency = ["inc-recent-low", "inc-mid-high", "inc-old-critical"]
        self.assertNotEqual(api_order, recency)

    def test_severity_and_confidence_are_independent_values(self):
        connection = self._seed(_incident("inc-split", "critical", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-split",
            {
                "incident_id": "inc-split",
                "diagnostic_confidence": "low",
                "leading_hypothesis": {"statement": "Provider degradation"},
            },
            "diagnosed",
        )
        item = self.app.queue()["incidents"][0]
        self.assertEqual(item["severity"], "critical")
        self.assertEqual(item["diagnostic_confidence"], "low")
        self.assertNotEqual(item["severity"], item["diagnostic_confidence"])
        self.assertNotIn("priority_badge", item)
        self.assertNotIn("combined_score", item)
        detail = self.app.detail("inc-split")
        self.assertEqual(detail["incident"]["severity"], "critical")
        self.assertEqual(detail["investigation"]["result"]["diagnostic_confidence"], "low")
        html = (ROOT / "surfaces" / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-severity", js)
        self.assertIn("data-confidence", js)
        self.assertIn("CRITICAL incident with LOW confidence", html)

    def test_agent_unavailable_still_renders_with_narrative_marked_unavailable(self):
        connection = self._seed(_incident("inc-degraded", "critical", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-degraded",
            {"incident_id": "inc-degraded", "leading_hypothesis": {"statement": "unavailable"}},
            "agent_unavailable",
            trail=[_trail_entry(), _trail_entry(2)],
        )
        detail = self.app.detail("inc-degraded")
        self.assertEqual(detail["incident"]["incident_id"], "inc-degraded")
        self.assertEqual(detail["incident"]["financial_impact"]["gmv_at_risk"]["amount"], 28000.0)
        self.assertEqual(detail["incident"]["affected_cohort"]["provider"], "provider-p2")
        self.assertEqual(len(detail["evidence_trail"]), 2)
        self.assertEqual(detail["evidence_trail"][0]["query_id"], "q_cohort_metrics_1")
        self.assertEqual(detail["investigation"]["outcome"], "agent_unavailable")
        self.assertFalse(detail["investigation"]["narrative_available"])
        self.assertFalse(detail["questions"]["narrative_available"])
        self.assertIsNone(detail["questions"]["what_probably_caused_it"])
        self.assertIsNotNone(detail["questions"]["what_changed"])
        self.assertIsNotNone(detail["questions"]["how_much_it_matters"])

    def test_missing_slack_webhook_logs_payload_and_marks_not_configured(self):
        incident = _incident("inc-high", "high", "2026-08-29T10:00:00Z")
        recorded = []

        def capture(message):
            recorded.append(message)

        outcome = notify_slack(
            {"incident_id": "inc-high", "severity": "high", "gmv_at_risk": {"amount": 28000.0}},
            webhook_url="",
            log=capture,
        )
        self.assertEqual(outcome["status"], "not_configured")
        self.assertEqual(outcome["channel"], "slack")
        self.assertEqual(len(recorded), 1)
        self.assertIn("payload that would have been sent", recorded[0])
        self.assertIn("inc-high", recorded[0])
        self.assertIn("28000", recorded[0])
        outcomes = escalate(incident, slack_url="", log=capture)
        self.assertTrue(any(item["channel"] == "slack" and item["status"] == "not_configured" for item in outcomes))

    def test_failing_channel_does_not_block_or_raise(self):
        incident = _incident("inc-high", "high", "2026-08-29T10:00:00Z")

        def boom(url, payload):
            raise RuntimeError("webhook down")

        outcomes = escalate(incident, slack_url="http://127.0.0.1:9/does-not-exist", poster=boom)
        statuses = {item["channel"]: item["status"] for item in outcomes}
        self.assertEqual(statuses["dashboard"], "delivered")
        self.assertEqual(statuses["slack"], "failed")
        self.assertNotIn("phone", statuses)
        self.app.overview()

    def test_no_endpoint_computes_a_metric_absent_from_the_source(self):
        source_change = {
            "metric": "payment_approval_conversion",
            "expected": 0.8123,
            "actual": 0.1765,
            "unit": "ratio",
        }
        source_financial = {
            "attempted_value": {"amount": 99991.25, "currency": "USD"},
            "gmv_at_risk": {"amount": 4242.5, "currency": "USD"},
        }
        self._seed(
            _incident(
                "inc-source",
                "medium",
                "2026-08-29T10:00:00Z",
                change=source_change,
                financial_impact=source_financial,
            )
        )
        payload = self.app.overview()
        self.assertEqual(payload["current_conversion"], 0.1765)
        self.assertEqual(payload["expected_conversion"], 0.8123)
        self.assertEqual(payload["gmv"]["amount"], 99991.25)
        self.assertEqual(payload["estimated_gmv_at_risk"]["amount"], 4242.5)
        self.assertEqual(payload["change"], source_change)
        self.assertNotIn("absolute_delta", payload["change"])
        self.assertNotIn("conversion_drop", payload)
        self.assertNotIn("priority_score", payload)
        detail = self.app.detail("inc-source")
        self.assertEqual(detail["incident"]["change"], source_change)
        self.assertEqual(detail["incident"]["financial_impact"], source_financial)

    def test_judge_trigger_reports_honestly_when_injection_is_not_wired(self):
        result = fire_hidden_incident(loader=lambda: None)
        self.assertFalse(result["wired"])
        self.assertFalse(result["fired"])
        self.assertIn("not wired", result["message"])
        status, payload = self.app.handle("POST", "/api/trigger", {"scenario_id": "must-be-ignored"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["wired"])
        self.assertEqual(payload["message"], "injection is not wired")

    def test_critical_severity_falls_back_to_dashboard_call_without_telephony(self):
        self._seed(_incident("inc-call", "critical", "2026-08-29T10:00:00Z"))
        detail = self.app.detail("inc-call")
        channels = {event["channel"]: event["status"] for event in detail["escalation"]}
        self.assertEqual(channels["dashboard"], "delivered")
        self.assertEqual(channels["slack"], "not_configured")
        self.assertEqual(channels["phone"], "fallback_dashboard")
        calls = self.app.pending_calls()["calls"]
        self.assertEqual(calls[0]["incident_id"], "inc-call")
        ack = self.app.acknowledge_call("inc-call")
        self.assertTrue(ack["acknowledged"])
        self.assertEqual(self.app.pending_calls()["calls"], [])

    def test_in_process_server_serves_overview_and_static_files(self):
        os.environ["CLEARWAVE_SURFACES_QUIET"] = "1"
        self.addCleanup(os.environ.pop, "CLEARWAVE_SURFACES_QUIET", None)
        self._seed(_incident("inc-http", "low", "2026-08-29T10:00:00Z"))
        httpd = make_server(self.db, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        port = httpd.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/overview", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["incidents"][0]["incident_id"], "inc-http")
        with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Control Tower", page)
        self.assertIn("Fire hidden incident", page)


class StaticContractTests(unittest.TestCase):
    def test_dashboard_has_no_remote_assets(self):
        static = ROOT / "surfaces" / "static"
        for path in static.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("https://", text)
            self.assertNotIn("http://", text)
            self.assertNotIn("cdn.", text)
            self.assertNotIn("fonts.googleapis", text)


if __name__ == "__main__":
    unittest.main()
