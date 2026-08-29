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
from surfaces.escalation import (
    TWILIO_ENV_VARS,
    TWILIO_TWIML_URL_ENV,
    escalate,
    notify_slack,
    place_call,
    slack_blocks,
    twiml_for,
    twilio_provider,
)
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
        connection = self._seed(
            _incident("inc-recent-low", "low", "2026-08-29T12:00:00Z"),
            _incident("inc-old-critical", "critical", "2026-08-29T08:00:00Z"),
            _incident("inc-mid-high", "high", "2026-08-29T11:00:00Z"),
        )
        ordered = [item["incident_id"] for item in list_incidents(connection)]
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


class SlackBlockKitTests(unittest.TestCase):
    def test_severity_and_confidence_render_in_separate_blocks(self):
        payload = {
            "incident_id": "inc-2026-08-29-001",
            "severity": "critical",
            "change": {
                "metric": "payment_approval_conversion",
                "expected": 0.92,
                "actual": 0.64,
            },
            "affected_cohort": {"merchant_id": "merchant-a", "provider": "provider-p2"},
            "financial_impact": {
                "gmv_at_risk": {"amount": 28000.0, "currency": "USD"},
                "loss_per_hour": {"amount": 112000.0, "currency": "USD"},
            },
            "onset": "2026-08-29T10:00:00Z",
            "diagnostic_confidence": "medium",
            "leading_hypothesis": {"statement": "Provider P2 degradation is the leading explanation."},
            "competing_explanations": [{"explanation": "Bank X over-decline cannot be ruled out."}],
            "recommended_next_action": {"action": "Investigate Provider P2.", "urgency": "now"},
        }
        message = slack_blocks(payload)
        self.assertEqual(message["channel"], "#control-tower")
        rendered = json.dumps(message)
        self.assertIn("CRITICAL", rendered)
        self.assertIn("inc-2026-08-29-001", rendered)
        self.assertIn("28,000", rendered)
        self.assertIn("112,000", rendered)
        self.assertIn("Merchant A", rendered)
        body = message["attachments"][0]["blocks"]
        self.assertEqual(message["attachments"][0]["color"], "#DC2626")
        severity_block = next(b for b in body if b["type"] == "header")
        hypothesis_block = next(b for b in body if "Possible cause" in json.dumps(b))
        not_ruled_out_block = next(b for b in body if "Not ruled out" in json.dumps(b))
        self.assertIn("CRITICAL", severity_block["text"]["text"])
        self.assertNotIn("medium", severity_block["text"]["text"])
        self.assertIn("medium confidence", hypothesis_block["text"]["text"])
        self.assertNotIn("Bank X over-decline", hypothesis_block["text"]["text"])
        self.assertIn("Bank X over-decline", not_ruled_out_block["text"]["text"])

    def test_citations_render_as_verified_against_sources(self):
        payload = {
            "incident_id": "inc-x",
            "severity": "high",
            "citations": {
                "decline_breakdown": "decline_breakdown:q_decline_breakdown_1",
                "operational_metrics": "operational_metrics:q_operational_metrics_1",
            },
        }
        rendered = json.dumps(slack_blocks(payload))
        self.assertIn("Verified against", rendered)
        self.assertIn("decline breakdown", rendered)
        self.assertIn("operational metrics", rendered)

    def test_no_citations_omits_verified_against_line(self):
        rendered = json.dumps(slack_blocks({"incident_id": "inc-x", "severity": "low"}))
        self.assertNotIn("Verified against", rendered)

    def test_sparse_payload_never_raises(self):
        message = slack_blocks({"incident_id": "inc-x"})
        self.assertEqual(message["channel"], "#control-tower")
        body = message["attachments"][0]["blocks"]
        self.assertTrue(any(b["type"] == "header" for b in body))

    def test_notify_slack_posts_block_kit_when_configured(self):
        captured = {}

        def poster(url, message):
            captured["url"] = url
            captured["message"] = message

        payload = {"incident_id": "inc-x", "severity": "high", "change": {}}
        outcome = notify_slack(payload, webhook_url="https://hooks.slack.test/x", poster=poster)
        self.assertEqual(outcome["status"], "delivered")
        self.assertEqual(captured["url"], "https://hooks.slack.test/x")
        self.assertIn("blocks", captured["message"])


class TwilioPhoneTests(unittest.TestCase):
    def setUp(self):
        for name in (*TWILIO_ENV_VARS, TWILIO_TWIML_URL_ENV):
            os.environ.pop(name, None)

    def test_twiml_is_silent_and_well_formed(self):
        body = twiml_for({"incident_id": "inc-x"})
        self.assertIn("<Pause", body)
        self.assertNotIn("<Say", body)

    def test_twilio_provider_posts_call_with_basic_auth(self):
        captured = {}

        def poster(account_sid, body, headers):
            captured["account_sid"] = account_sid
            captured["body"] = body
            captured["headers"] = headers

        provider = twilio_provider(
            account_sid="ACtest",
            auth_token="secret",
            from_number="+15550000001",
            to_number="+15550000002",
            poster=poster,
        )
        provider({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertEqual(captured["account_sid"], "ACtest")
        self.assertIn("Authorization", captured["headers"])
        self.assertTrue(captured["headers"]["Authorization"].startswith("Basic "))
        body_text = captured["body"].decode("utf-8")
        self.assertIn("To=%2B15550000002", body_text)
        self.assertIn("Pause", body_text)

    def test_twilio_provider_raises_when_credentials_incomplete(self):
        provider = twilio_provider(account_sid="ACtest", auth_token="", from_number="", to_number="")
        with self.assertRaises(RuntimeError):
            provider({"incident_id": "inc-x"}, {"incident_id": "inc-x"})

    def test_twilio_provider_uses_twiml_bin_url_when_configured(self):
        # Verified against a real Twilio trial account: the Calls API rejects
        # inline Twiml with HTTP 400 "trial accounts have limited parameter
        # access". A TwiML Bin URL is required on trial accounts.
        captured = {}

        def poster(account_sid, body, headers):
            captured["body"] = body.decode("utf-8")

        provider = twilio_provider(
            account_sid="ACtest",
            auth_token="secret",
            from_number="+15550000001",
            to_number="+15550000002",
            twiml_url="https://handler.twilio.com/twiml/EHtest",
            poster=poster,
        )
        provider({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertIn("Url=https%3A%2F%2Fhandler.twilio.com%2Ftwiml%2FEHtest", captured["body"])
        self.assertNotIn("Twiml=", captured["body"])

    def test_twilio_provider_falls_back_to_inline_twiml_without_a_bin_url(self):
        captured = {}

        def poster(account_sid, body, headers):
            captured["body"] = body.decode("utf-8")

        provider = twilio_provider(
            account_sid="ACtest",
            auth_token="secret",
            from_number="+15550000001",
            to_number="+15550000002",
            poster=poster,
        )
        provider({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertIn("Twiml=", captured["body"])
        self.assertNotIn("Url=", captured["body"])

    def test_place_call_auto_wires_twilio_from_environment(self):
        os.environ["CLEARWAVE_TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["CLEARWAVE_TWILIO_AUTH_TOKEN"] = "secret"
        os.environ["CLEARWAVE_TWILIO_FROM_NUMBER"] = "+15550000001"
        os.environ["CLEARWAVE_TWILIO_TO_NUMBER"] = "+15550000002"
        self.addCleanup(lambda: [os.environ.pop(name, None) for name in TWILIO_ENV_VARS])
        captured = {}

        def fake_urlopen_poster(account_sid, body, headers):
            captured["account_sid"] = account_sid

        import surfaces.escalation as escalation_module

        original = escalation_module._post_twilio_call
        escalation_module._post_twilio_call = fake_urlopen_poster
        self.addCleanup(setattr, escalation_module, "_post_twilio_call", original)

        outcome = place_call({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertEqual(outcome["status"], "delivered")
        self.assertEqual(captured["account_sid"], "ACtest")

    def test_place_call_auto_wires_the_twiml_bin_url_when_set(self):
        os.environ["CLEARWAVE_TWILIO_ACCOUNT_SID"] = "ACtest"
        os.environ["CLEARWAVE_TWILIO_AUTH_TOKEN"] = "secret"
        os.environ["CLEARWAVE_TWILIO_FROM_NUMBER"] = "+15550000001"
        os.environ["CLEARWAVE_TWILIO_TO_NUMBER"] = "+15550000002"
        os.environ[TWILIO_TWIML_URL_ENV] = "https://handler.twilio.com/twiml/EHtest"
        self.addCleanup(
            lambda: [os.environ.pop(name, None) for name in (*TWILIO_ENV_VARS, TWILIO_TWIML_URL_ENV)]
        )
        captured = {}

        def fake_urlopen_poster(account_sid, body, headers):
            captured["body"] = body.decode("utf-8")

        import surfaces.escalation as escalation_module

        original = escalation_module._post_twilio_call
        escalation_module._post_twilio_call = fake_urlopen_poster
        self.addCleanup(setattr, escalation_module, "_post_twilio_call", original)

        outcome = place_call({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertEqual(outcome["status"], "delivered")
        self.assertIn("Url=https%3A%2F%2Fhandler.twilio.com%2Ftwiml%2FEHtest", captured["body"])

    def test_place_call_falls_back_without_credentials(self):
        outcome = place_call({"incident_id": "inc-x"}, {"incident_id": "inc-x"})
        self.assertEqual(outcome["status"], "fallback_dashboard")


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
