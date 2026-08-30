"""Offline tests for the W4 surfaces layer."""

from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import warnings
from pathlib import Path
from unittest.mock import patch

from investigation.store import insert_incident, persist_result
from surfaces.escalation import (
    DEFAULT_SLACK_CHANNEL,
    TWILIO_ENV_VARS,
    TWILIO_TWIML_URL_ENV,
    channels_for,
    escalate,
    notify_slack,
    place_call,
    slack_blocks,
    twiml_for,
    twilio_provider,
)
from surfaces import escalation as escalation_module
from surfaces.escalation import _money, _truncate
from surfaces.inject import (
    INJECTED_INCIDENT,
    STAGE_COLLAPSE,
    STAGE_DEVELOPING,
    acknowledgement,
    fire_hidden_incident,
    injected_incident_command,
)
from surfaces import present
from surfaces.present import cohort_scope_label, merchant_health
from surfaces.server import SurfacesApp, make_server
from surfaces.store import (
    ESCALATABLE_STATES,
    connect,
    ensure_escalation,
    list_incidents,
    load_escalation,
    load_incident,
    load_investigation,
)
from worker.helpers.control import CONTROL_TOPIC
from worker.inject import start_command, stop_command


ROOT = Path(__file__).resolve().parents[1]


def _stop_server(httpd, thread) -> None:
    """
    Stop the HTTP server, wait for its serving thread to finish, and close its socket.
    """
    httpd.shutdown()
    thread.join(timeout=5)
    httpd.server_close()


def _incident(incident_id, severity, onset, merchant="merchant-a", **fields):
    """
    Build a representative incident record for tests.
    
    Parameters:
    	incident_id: Identifier for the incident.
    	severity: Incident severity.
    	onset: Incident onset timestamp.
    	merchant: Merchant identifier for the affected cohort.
    	**fields: Field overrides applied to the generated record.
    
    Returns:
    	dict: A populated incident record.
    """
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


def _diagnosis(incident_id, outcome="diagnosed"):
    return {
        "incident_id": incident_id,
        "confirmed_facts": [],
        "leading_hypothesis": {
            "statement": "Provider P2 degradation is the leading explanation.",
            "evidence": [],
        },
        "supporting_evidence": [],
        "competing_explanations": [],
        "why_ambiguity_exists": {"statement": "Sibling traffic is limited.", "evidence": []},
        "missing_evidence": [],
        "diagnostic_confidence": "medium",
        "recommended_next_action": {
            "action": "Investigate Provider P2 before broad rerouting.",
            "urgency": "now",
            "basis": [],
        },
        "outcome": outcome,
    }


def _watch(incident_id="inc-watch", merchant="merchant-w", **fields):
    """A C3 row in `lifecycle_state: watching`, in the shape detector/detect.py
    stores one: forced to `low`, carrying a projected figure under its own key,
    and carrying both floor vectors so the page can say why it is not yet an
    incident."""
    record = _incident(incident_id, "low", "2026-08-29T09:30:00Z", merchant=merchant)
    record["lifecycle_state"] = "watching"
    record["financial_impact"]["projected_loss_per_hour"] = {
        "amount": 15798.36,
        "currency": "USD",
        "basis": "the measured conversion shortfall applied to this cohort's typical "
        "hourly attempted value. It is not money already lost, and it never ranks severity.",
    }
    record["detection"] = {
        "detection_floors": {
            "has_measurement": True,
            "z_min": False,
            "absolute_drop_min": True,
            "volume_min": True,
        },
        "watch": {
            "reasons": ["conversion_near_miss"],
            "watch_floors": {
                "has_measurement": True,
                "not_already_an_incident": True,
                "volume_min": True,
                "statistically_real": True,
                "materially_large": True,
                "worsening": True,
            },
            "not_yet_met": [],
            "trajectory": 1,
            "leading_indicators": {"timeout_share": {"degraded": False}},
            "degraded_leading_indicators": [],
            "statement": "This cohort is unusual for itself against its last hour and is getting worse.",
        },
    }
    record.update(fields)
    return record


class SurfacesTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        os.environ.pop("CLEARWAVE_SLACK_WEBHOOK_URL", None)
        os.environ.pop("CLEARWAVE_PHONE_PROVIDER", None)
        for name in (*TWILIO_ENV_VARS, TWILIO_TWIML_URL_ENV):
            os.environ.pop(name, None)
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
        self.assertEqual(statuses["phone"], "fallback_dashboard")
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

    def test_judge_trigger_publishes_w1s_own_start_command(self):
        published = []
        result = fire_hidden_incident(True, publisher=published.append)
        self.assertTrue(result["wired"])
        self.assertTrue(result["delivered"])
        self.assertTrue(result["fired"])
        self.assertTrue(result["active"])
        self.assertEqual(result["stage"], "collapse")
        self.assertEqual(published, [start_command("merchant-b", provider="adyen",
                                                   decline_reason="provider_timeout",
                                                   decline_probability=STAGE_COLLAPSE)])
        self.assertEqual(result["topic"], CONTROL_TOPIC)
        self.assertEqual(result["target"], INJECTED_INCIDENT)

    def test_toggling_off_publishes_w1s_stop_command(self):
        published = []
        result = fire_hidden_incident(False, publisher=published.append)
        self.assertTrue(result["delivered"])
        self.assertFalse(result["fired"])
        self.assertFalse(result["active"])
        self.assertEqual(result["stage"], "clear")
        self.assertEqual(published, [{"merchant_id": "merchant-b", "action": "stop"}])
        self.assertEqual(published[0], stop_command("merchant-b"))

    def test_developing_stage_publishes_the_mild_probability(self):
        published = []
        result = fire_hidden_incident(publisher=published.append, stage="developing")
        self.assertTrue(result["delivered"])
        self.assertTrue(result["active"])
        self.assertEqual(result["stage"], "developing")
        self.assertEqual(published[0]["decline_probability"], STAGE_DEVELOPING)
        self.assertEqual(published, [start_command(
            "merchant-b",
            provider="adyen",
            decline_reason="provider_timeout",
            decline_probability=STAGE_DEVELOPING,
        )])

    def test_collapse_stage_publishes_the_near_total_break(self):
        published = []
        result = fire_hidden_incident(publisher=published.append, stage="collapse")
        self.assertEqual(result["stage"], "collapse")
        self.assertEqual(published[0]["decline_probability"], STAGE_COLLAPSE)

    def test_clear_stage_publishes_the_stop_command(self):
        published = []
        result = fire_hidden_incident(publisher=published.append, stage="clear")
        self.assertFalse(result["active"])
        self.assertEqual(result["stage"], "clear")
        self.assertEqual(published, [stop_command("merchant-b")])

    def test_the_published_command_carries_no_scenario_identifier(self):
        # C6 quarantine: what crosses to W1 is a cohort scope and an effect.
        # Nothing downstream of the worker may learn which scenario this is.
        for active in (True, False):
            command = injected_incident_command(active)
            rendered = json.dumps(command).lower()
            self.assertNotIn("scenario", rendered)
            self.assertNotIn("ground_truth", rendered)
            self.assertLessEqual(
                set(command),
                {"merchant_id", "action", "scope", "effect", "decline_reason", "latency_ms", "decline_probability"},
            )

    def test_an_unreachable_broker_never_claims_a_scenario_fired(self):
        def unreachable(command):
            raise RuntimeError("no broker at localhost:9092")

        result = fire_hidden_incident(True, publisher=unreachable)
        self.assertFalse(result["delivered"])
        self.assertFalse(result["fired"])
        self.assertFalse(result["active"])
        self.assertEqual(result["stage"], "clear")
        self.assertEqual(result["requested"], "collapse")
        self.assertIn("no broker", result["error"])
        self.assertIn("Nothing was injected", result["message"])

        developing = fire_hidden_incident(publisher=unreachable, stage="developing")
        self.assertFalse(developing["delivered"])
        self.assertFalse(developing["fired"])
        self.assertEqual(developing["requested"], "developing")
        self.assertEqual(developing["stage"], "clear")
        self.assertIn("Nothing was injected", developing["message"])

    def test_the_api_carries_the_on_off_intent_and_ignores_everything_else(self):
        published = []
        self.app_publisher(published)
        status, on = self.app.handle(
            "POST", "/api/trigger", {"active": True, "scenario_id": "must-be-ignored"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(on["active"])
        self.assertEqual(on["stage"], "collapse")
        self.assertEqual(published[-1]["decline_probability"], STAGE_COLLAPSE)
        status, off = self.app.handle("POST", "/api/judge/trigger", {"active": False})
        self.assertEqual(status, 200)
        self.assertFalse(off["active"])
        self.assertEqual(off["stage"], "clear")
        self.assertEqual([command["action"] for command in published], ["start", "stop"])
        self.assertNotIn("scenario_id", json.dumps(published))
        # A body-less POST keeps the old "fire it" meaning of this path.
        self.app.handle("POST", "/api/trigger", None)
        self.assertEqual(published[-1]["action"], "start")
        self.assertEqual(published[-1]["decline_probability"], STAGE_COLLAPSE)

    def test_legacy_boolean_body_still_publishes_the_full_break(self):
        published = []
        self.app_publisher(published)
        _, on = self.app.handle("POST", "/api/trigger", {"active": True})
        self.assertEqual(on["stage"], "collapse")
        self.assertEqual(published[-1]["decline_probability"], STAGE_COLLAPSE)
        self.assertEqual(published[-1], start_command(
            "merchant-b",
            provider="adyen",
            decline_reason="provider_timeout",
            decline_probability=STAGE_COLLAPSE,
        ))

    def test_the_api_publishes_each_requested_stage(self):
        published = []
        self.app_publisher(published)
        _, developing = self.app.handle("POST", "/api/trigger", {"stage": "developing"})
        self.assertEqual(developing["stage"], "developing")
        self.assertEqual(published[-1]["decline_probability"], STAGE_DEVELOPING)
        _, collapse = self.app.handle("POST", "/api/trigger", {"stage": "collapse"})
        self.assertEqual(collapse["stage"], "collapse")
        self.assertEqual(published[-1]["decline_probability"], STAGE_COLLAPSE)
        _, cleared = self.app.handle("POST", "/api/trigger", {"stage": "clear"})
        self.assertEqual(cleared["stage"], "clear")
        self.assertEqual(published[-1], stop_command("merchant-b"))

    def test_the_toggle_state_survives_a_page_reload_and_a_failed_publish(self):
        published = []
        self.app_publisher(published)
        idle = self.app.handle("GET", "/api/trigger")[1]
        self.assertFalse(idle["active"])
        self.assertEqual(idle["stage"], "clear")
        self.app.handle("POST", "/api/trigger", {"stage": "developing"})
        state = self.app.handle("GET", "/api/trigger")[1]
        self.assertTrue(state["active"])
        self.assertEqual(state["stage"], "developing")
        self.assertEqual(state["target"], INJECTED_INCIDENT)

        self.app.handle("POST", "/api/trigger", {"stage": "collapse"})
        collapsed = self.app.handle("GET", "/api/trigger")[1]
        self.assertEqual(collapsed["stage"], "collapse")
        self.assertTrue(collapsed["active"])

        import surfaces.inject as inject_module

        def unreachable(command):
            raise RuntimeError("broker went away")

        inject_module._publish = unreachable
        failed = self.app.handle("POST", "/api/trigger", {"stage": "clear"})[1]
        self.assertFalse(failed["delivered"])
        self.assertIn("Nothing was injected", failed["message"])
        # The clear never landed, so the control must not pretend it is off.
        held = self.app.handle("GET", "/api/trigger")[1]
        self.assertTrue(held["active"])
        self.assertEqual(held["stage"], "collapse")

    def app_publisher(self, sink):
        """Point the app's injection at a list instead of a broker."""
        import surfaces.inject as inject_module

        original = inject_module._publish
        inject_module._publish = sink.append
        self.addCleanup(setattr, inject_module, "_publish", original)

    def test_critical_severity_falls_back_to_dashboard_call_without_telephony(self):
        connection = self._seed(_incident("inc-call", "critical", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-call",
            _diagnosis("inc-call"),
            "diagnosed",
            trail=[_trail_entry()],
        )
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

    def test_dashboard_read_before_diagnosis_does_not_fire_or_record_escalation(self):
        connection = self._seed(_incident("inc-early", "critical", "2026-08-29T10:00:00Z"))
        detail = self.app.detail("inc-early")
        self.assertEqual(detail["escalation"], [])
        self.assertEqual(load_escalation(connection, "inc-early"), [])
        self.app.overview()
        self.app.queue()
        self.assertEqual(load_escalation(connection, "inc-early"), [])

    def test_read_before_diagnosis_then_diagnosis_fires_once_with_complete_payload(self):
        # Hazardous demo ordering: the dashboard is already polling when detection
        # lands, so the first read happens with no C4 result. That read must not
        # lock an empty notification; the later read after diagnosis is the one
        # that fires, once, with the complete payload.
        connection = self._seed(_incident("inc-order", "critical", "2026-08-29T10:00:00Z"))
        early = self.app.detail("inc-order")
        self.assertEqual(early["escalation"], [])
        persist_result(
            connection,
            "inc-order",
            _diagnosis("inc-order"),
            "diagnosed",
            trail=[_trail_entry()],
        )
        later = self.app.detail("inc-order")
        events = later["escalation"]
        self.assertEqual({event["channel"] for event in events}, {"dashboard", "slack", "phone"})
        for event in events:
            payload = event["payload"]
            self.assertEqual(
                payload["leading_hypothesis"]["statement"],
                "Provider P2 degradation is the leading explanation.",
            )
            self.assertEqual(payload["diagnostic_confidence"], "medium")
            self.assertEqual(
                payload["recommended_next_action"]["action"],
                "Investigate Provider P2 before broad rerouting.",
            )
            self.assertIn("cohort_metrics", payload["citations"])
        self.assertEqual(len(load_escalation(connection, "inc-order")), 3)
        again = self.app.detail("inc-order")
        self.assertEqual(
            [(event["channel"], event["status"], event["payload"]) for event in again["escalation"]],
            [(event["channel"], event["status"], event["payload"]) for event in events],
        )
        self.assertEqual(len(load_escalation(connection, "inc-order")), 3)

    def test_degraded_diagnosis_still_escalates(self):
        connection = self._seed(_incident("inc-degraded-esc", "high", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-degraded-esc",
            {
                "incident_id": "inc-degraded-esc",
                "leading_hypothesis": {
                    "statement": "Causal investigation unavailable: agent down",
                },
                "diagnostic_confidence": "low",
                "recommended_next_action": {
                    "action": "Review the deterministic incident facts",
                    "urgency": "now",
                },
            },
            "agent_unavailable",
            trail=[_trail_entry()],
        )
        events = self.app.detail("inc-degraded-esc")["escalation"]
        self.assertTrue(events)
        payload = events[0]["payload"]
        # Escalation still fires on a degraded diagnosis (ADR 0010), but the
        # placeholder narrative ("Causal investigation unavailable: ...") must
        # never be sent as if it were a real cause - that would fabricate a
        # diagnosis for exactly the incident that has none.
        self.assertIsNone(payload["leading_hypothesis"])
        self.assertIsNone(payload["diagnostic_confidence"])
        self.assertIsNone(payload["recommended_next_action"])
        self.assertEqual(payload["competing_explanations"], [])

    def test_agent_unavailable_detail_and_queue_both_omit_the_raw_narrative(self):
        # Same leak as above, one layer down: the dashboard's raw investigation
        # result and the queue's confidence badge must not show the degrade
        # placeholder either, even though the narrative banner correctly says
        # it is unavailable.
        connection = self._seed(_incident("inc-degraded-ui", "critical", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-degraded-ui",
            {
                "incident_id": "inc-degraded-ui",
                "leading_hypothesis": {"statement": "Causal investigation unavailable: agent down"},
                "diagnostic_confidence": "low",
            },
            "agent_unavailable",
            trail=[_trail_entry()],
        )
        detail = self.app.detail("inc-degraded-ui")
        self.assertFalse(detail["investigation"]["narrative_available"])
        self.assertIsNone(detail["investigation"]["result"])
        queue_item = self.app.queue()["incidents"][0]
        self.assertIsNone(queue_item["diagnostic_confidence"])

    def test_ambiguous_outcome_still_renders_its_real_narrative(self):
        # The C5 contract nulls narrative fields only for agent_unavailable.
        # An ambiguous result has a real narrative, just with more caveats -
        # it must render normally, not be treated as unavailable.
        connection = self._seed(_incident("inc-ambiguous", "high", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-ambiguous",
            {
                "incident_id": "inc-ambiguous",
                "leading_hypothesis": {"statement": "Provider or issuer, evidence cannot separate them."},
                "diagnostic_confidence": "low",
            },
            "ambiguous",
            trail=[_trail_entry()],
        )
        detail = self.app.detail("inc-ambiguous")
        self.assertTrue(detail["investigation"]["narrative_available"])
        self.assertEqual(
            detail["investigation"]["result"]["leading_hypothesis"]["statement"],
            "Provider or issuer, evidence cannot separate them.",
        )
        self.assertEqual(self.app.queue()["incidents"][0]["diagnostic_confidence"], "low")

    def test_insufficient_evidence_outcome_still_renders_its_real_narrative(self):
        connection = self._seed(_incident("inc-insufficient", "high", "2026-08-29T10:00:00Z"))
        persist_result(
            connection,
            "inc-insufficient",
            {
                "incident_id": "inc-insufficient",
                "leading_hypothesis": {"statement": "Not enough evidence yet to name a leading cause."},
                "diagnostic_confidence": "low",
            },
            "insufficient_evidence",
            trail=[_trail_entry()],
        )
        detail = self.app.detail("inc-insufficient")
        self.assertTrue(detail["investigation"]["narrative_available"])
        self.assertIsNotNone(detail["investigation"]["result"])

    def test_channels_for_pins_full_severity_binding_and_unknown_fallback(self):
        # Pins the complete policy (critical and high both phone; low/medium dashboard-only;
        # unknown stays conservative) so the binding is tested, not only commented.
        from surfaces.escalation import channels_for
        self.assertEqual(channels_for("low"), ("dashboard",))
        self.assertEqual(channels_for("medium"), ("dashboard",))
        self.assertEqual(channels_for("high"), ("dashboard", "slack", "phone"))
        self.assertEqual(channels_for("critical"), ("dashboard", "slack", "phone"))
        self.assertEqual(channels_for("unknown"), ("dashboard",))
        self.assertEqual(channels_for("HIGH"), ("dashboard", "slack", "phone"))
        self.assertEqual(channels_for(None), ("dashboard",))
        self.assertEqual(channels_for(""), ("dashboard",))

    def test_in_process_server_serves_overview_and_static_files(self):
        os.environ["CLEARWAVE_SURFACES_QUIET"] = "1"
        self.addCleanup(os.environ.pop, "CLEARWAVE_SURFACES_QUIET", None)
        self._seed(_incident("inc-http", "low", "2026-08-29T10:00:00Z"))
        httpd = make_server(self.db, host="127.0.0.1", port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(_stop_server, httpd, thread)
        port = httpd.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/overview", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["incidents"][0]["incident_id"], "inc-http")
        with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Control Tower", page)
        self.assertIn("Developing deviation", page)
        self.assertIn("Collapse", page)
        self.assertIn("Clear", page)
        self.assertIn("simulated data produced by this project's simulator", page)
        self.assertIn("Nothing shown represents or implies a real incident", page)

    def test_provider_wide_cohort_is_not_an_unknown_merchant(self):
        incident = _incident(
            "inc-provider",
            "critical",
            "2026-08-29T10:00:00Z",
            affected_cohort={"provider": "provider-p2"},
        )
        connection = self._seed(incident)
        persist_result(
            connection,
            "inc-provider",
            _diagnosis("inc-provider"),
            "diagnosed",
            trail=[_trail_entry()],
        )
        row = self.app.merchants()["merchants"][0]
        self.assertIsNone(row["merchant_id"])
        self.assertNotEqual(row["merchant_id"], "unknown")
        self.assertEqual(row["scope_label"], "Provider P2")
        self.assertNotIn("unknown", row["scope_label"].lower())
        overview = self.app.overview()["merchant_health"][0]
        self.assertIsNone(overview["merchant_id"])
        self.assertEqual(overview["scope_label"], "Provider P2")
        payload = escalate(incident)[0]["payload"]
        self.assertIsNone(payload["merchant_id"])
        self.assertEqual(payload["scope_label"], "Provider P2")
        rendered = json.dumps(slack_blocks(payload))
        self.assertNotIn("Merchant unknown", rendered)
        self.assertIn("Provider P2", rendered)
        detail = self.app.detail("inc-provider")
        call_payload = detail["escalation"][0]["payload"]
        self.assertIsNone(call_payload["merchant_id"])
        self.assertEqual(call_payload["scope_label"], "Provider P2")

    def test_merchant_scoped_health_keeps_the_named_merchant(self):
        self._seed(_incident("inc-a", "high", "2026-08-29T10:00:00Z"))
        row = self.app.merchants()["merchants"][0]
        self.assertEqual(row["merchant_id"], "merchant-a")
        self.assertEqual(row["highest_severity"], "high")
        self.assertEqual(row["active_incident_count"], 1)
        payload = escalate(_incident("inc-a", "high", "2026-08-29T10:00:00Z"))[0]["payload"]
        self.assertEqual(payload["merchant_id"], "merchant-a")

    def test_non_merchant_cohorts_never_invent_a_merchant_identity(self):
        cases = [
            ({"provider": "provider-p2"}, "Provider P2"),
            ({"country": "CO"}, "Country CO"),
            ({"payment_method": "card"}, "Payment method card"),
            ({"issuing_bank": "bank-x"}, "Issuing bank bank-x"),
            ({}, "Platform-wide"),
        ]
        for cohort, label in cases:
            with self.subTest(cohort=cohort):
                incident = _incident(
                    "inc-scope",
                    "critical",
                    "2026-08-29T10:00:00Z",
                    affected_cohort=cohort,
                )
                rows = merchant_health([incident])
                self.assertEqual(len(rows), 1)
                self.assertIsNone(rows[0]["merchant_id"])
                self.assertEqual(rows[0]["scope_label"], label)
                self.assertNotIn("unknown", rows[0]["scope_label"].lower())
                self.assertEqual(cohort_scope_label(cohort), label)
                payload = escalate(incident)[0]["payload"]
                self.assertIsNone(payload["merchant_id"])
                self.assertEqual(payload["scope_label"], label)
                self.assertNotIn("Merchant unknown", json.dumps(slack_blocks(payload)))

    def test_merchant_and_provider_scopes_are_not_collapsed(self):
        rows = merchant_health(
            [
                _incident("inc-a", "high", "2026-08-29T10:00:00Z"),
                _incident(
                    "inc-p2",
                    "critical",
                    "2026-08-29T10:00:00Z",
                    affected_cohort={"provider": "provider-p2"},
                ),
            ]
        )
        by_id = {row["merchant_id"]: row for row in rows}
        self.assertEqual(set(by_id), {"merchant-a", None})
        self.assertEqual(by_id["merchant-a"]["incident_ids"], ["inc-a"])
        self.assertEqual(by_id[None]["scope_label"], "Provider P2")
        self.assertEqual(by_id[None]["incident_ids"], ["inc-p2"])
        self.assertNotIn("unknown", [row["merchant_id"] for row in rows])


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

    def test_money_formats_negative_amounts_with_leading_sign(self):
        self.assertEqual(_money({"amount": -28000.0, "currency": "USD"}), "-$28,000 USD")
        self.assertEqual(_money({"amount": 28000.0, "currency": "USD"}), "$28,000 USD")

    def test_truncate_helper_leaves_short_text_untouched(self):
        self.assertEqual(_truncate("short", 100), "short")
        self.assertIsNone(_truncate(None, 100))
        self.assertTrue(_truncate("x" * 200, 100).endswith("…"))
        self.assertLessEqual(len(_truncate("x" * 200, 100)), 100)

    def test_slack_blocks_truncates_a_long_hypothesis_statement(self):
        # LLM-authored narrative text has no max_length anywhere upstream
        # (investigation/contracts.py). Slack rejects the whole message over
        # ~3000 chars per section, silently failing the critical channel.
        # Shrink the real constant instead of building a 3000-char fixture.
        original = escalation_module.SECTION_TEXT_LIMIT
        escalation_module.SECTION_TEXT_LIMIT = 60
        self.addCleanup(setattr, escalation_module, "SECTION_TEXT_LIMIT", original)
        payload = {
            "incident_id": "inc-long",
            "severity": "critical",
            "leading_hypothesis": {"statement": "Provider P2 degradation. " * 20},
        }
        body = slack_blocks(payload)["attachments"][0]["blocks"]
        hypothesis_block = next(b for b in body if "Possible cause" in json.dumps(b))
        text = hypothesis_block["text"]["text"]
        self.assertLessEqual(len(text), 60)
        self.assertTrue(text.endswith("…"))

    def test_slack_blocks_truncates_joined_competing_explanations(self):
        original = escalation_module.SECTION_TEXT_LIMIT
        escalation_module.SECTION_TEXT_LIMIT = 60
        self.addCleanup(setattr, escalation_module, "SECTION_TEXT_LIMIT", original)
        payload = {
            "incident_id": "inc-long",
            "severity": "high",
            "competing_explanations": [
                {"explanation": "Bank X over-decline cannot be ruled out."},
                {"explanation": "Deployment regression on the router is also plausible."},
                {"explanation": "Retry amplification could independently explain the timeout spike."},
            ],
        }
        body = slack_blocks(payload)["attachments"][0]["blocks"]
        not_ruled_out_block = next(b for b in body if "Not ruled out" in json.dumps(b))
        text = not_ruled_out_block["text"]["text"]
        self.assertLessEqual(len(text), 60)
        self.assertTrue(text.endswith("…"))

    def test_slack_blocks_header_stays_under_the_plain_text_limit(self):
        original = escalation_module.HEADER_TEXT_LIMIT
        escalation_module.HEADER_TEXT_LIMIT = 20
        self.addCleanup(setattr, escalation_module, "HEADER_TEXT_LIMIT", original)
        payload = {
            "incident_id": "inc-long-scope",
            "severity": "critical",
            "affected_cohort": {
                "provider": "provider-p2",
                "country": "CO",
                "card_network": "mastercard",
                "payment_method": "card",
                "issuing_bank": "bank-x",
            },
        }
        body = slack_blocks(payload)["attachments"][0]["blocks"]
        header_block = next(b for b in body if b["type"] == "header")
        self.assertLessEqual(len(header_block["text"]["text"]), 20)


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


class DashboardEntrypointTests(unittest.TestCase):
    def test_dashboard_entrypoint_loads_dotenv_and_shell_values_win(self):
        import investigation.env as env_module
        import surfaces.server as server_module

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                "CLEARWAVE_SLACK_WEBHOOK_URL=https://file.example/hook\n"
                "CLEARWAVE_TWILIO_ACCOUNT_SID=ACfromfile\n",
                encoding="utf-8",
            )
            observed = {}

            class FakeServer:
                server_address = ("127.0.0.1", 8080)

                def __init__(self, *args, **kwargs):
                    observed["slack"] = os.environ.get("CLEARWAVE_SLACK_WEBHOOK_URL")
                    observed["sid"] = os.environ.get("CLEARWAVE_TWILIO_ACCOUNT_SID")

                def serve_forever(self):
                    return None

                def server_close(self):
                    return None

            with patch.dict(
                os.environ, {"CLEARWAVE_SLACK_WEBHOOK_URL": "https://shell.example/hook"}, clear=False
            ):
                os.environ.pop("CLEARWAVE_TWILIO_ACCOUNT_SID", None)
                with patch.object(env_module, "ROOT", Path(directory)), patch.object(
                    server_module, "ThreadingHTTPServer", FakeServer
                ), patch.object(sys, "argv", ["surfaces", "--db", str(Path(directory, "db"))]):
                    with self.assertRaises(SystemExit) as raised:
                        runpy.run_module("surfaces.__main__", run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(observed, {
                "slack": "https://shell.example/hook",
                "sid": "ACfromfile",
            })

    def test_server_module_entrypoint_loads_dotenv(self):
        import http.server
        import investigation.env as env_module

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text(
                "CLEARWAVE_SLACK_WEBHOOK_URL=https://server-file.example/hook\n",
                encoding="utf-8",
            )
            observed = {}

            class FakeServer:
                server_address = ("127.0.0.1", 8080)

                def __init__(self, *args, **kwargs):
                    observed["slack"] = os.environ.get("CLEARWAVE_SLACK_WEBHOOK_URL")

                def serve_forever(self):
                    return None

                def server_close(self):
                    return None

            with patch.dict(
                os.environ, {"CLEARWAVE_SLACK_WEBHOOK_URL": "https://server-shell.example/hook"}, clear=False
            ), patch.object(env_module, "ROOT", Path(directory)), patch.object(
                http.server, "ThreadingHTTPServer", FakeServer
            ), patch.object(sys, "argv", ["surfaces.server", "--db", str(Path(directory, "db"))]):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    with self.assertRaises(SystemExit) as raised:
                        runpy.run_module("surfaces.server", run_name="__main__")

            self.assertEqual(raised.exception.code, 0)
            self.assertEqual(observed["slack"], "https://server-shell.example/hook")


class StaticContractTests(unittest.TestCase):
    def test_dashboard_has_no_remote_assets(self):
        static = ROOT / "surfaces" / "static"
        for path in static.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("https://", text)
            self.assertNotIn("http://", text)
            self.assertNotIn("cdn.", text)
            self.assertNotIn("fonts.googleapis", text)

    def test_dashboard_does_not_render_a_missing_merchant_as_unknown(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('payload.merchant_id || "unknown"', js)
        self.assertIn("scope_label", js)
        self.assertIn("incidentScope", js)

    def test_dashboard_has_no_call_controls(self):
        static = ROOT / "surfaces" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        js = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")
        for blob in (html, js, css):
            self.assertNotIn("incoming-call", blob)
            self.assertNotIn("answer-call", blob)
            self.assertNotIn("incoming-copy", blob)
            self.assertNotIn("incoming-panel", blob)
        self.assertNotIn("dismissedCalls", js)
        self.assertNotIn("pending-call", html)

    def test_escalation_outcomes_are_shown_as_read_only_data(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("These are stored outcomes, not controls.", js)
        self.assertIn("event.channel", js)
        self.assertIn("event.status", js)
        self.assertIn("fallback_dashboard", js)

    def test_missing_recommended_action_is_honest_absence(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn(
            "Operator narrative is the stored recommended action, or unavailable if the agent failed.",
            js,
        )
        self.assertIn("questions.what_the_operator_should_do", js)
        self.assertIn("Investigation has not run yet.", js)

    def test_missing_investigation_is_not_labelled_agent_unavailable(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('outcome || "agent_unavailable"', js)
        self.assertNotIn("unavailableCopy", js)
        self.assertIn("Investigation has not run yet.", js)
        self.assertIn("Investigation is running.", js)
        self.assertIn("Narrative unavailable because the investigation agent failed.", js)

    def test_missing_confidence_is_not_labelled_none(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('confidence || "none"', js)
        self.assertIn('return "not in store"', js)
        self.assertIn("awaiting investigation", js)
        self.assertIn('"confidence " + confValue', js)

    def test_live_dashboard_does_not_bake_mockup_figures(self):
        static = ROOT / "surfaces" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        js = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")
        for blob in (html, js, css):
            self.assertNotIn("78,919", blob)
            self.assertNotIn("78919", blob)
            self.assertNotIn("19,785", blob)
            self.assertNotIn("inc-2026-08-30-c2b28a30", blob)

    def test_tam_mockup_remains_as_design_reference(self):
        path = ROOT / "surfaces" / "static" / "tam-dashboard.html"
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1000)

    def test_the_judge_control_is_a_toggle_with_both_states_in_the_markup(self):
        html = (ROOT / "surfaces" / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "surfaces" / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('data-stage="developing"', html)
        self.assertIn('data-stage="collapse"', html)
        self.assertIn('data-stage="clear"', html)
        self.assertIn("Developing deviation", html)
        self.assertIn("Collapse", html)
        self.assertRegex(html, r'data-stage="clear"[\s\S]*?Clear')
        self.assertIn('aria-pressed="false"', html)
        self.assertIn('data-on="false"', html)
        self.assertIn('data-stage', js)
        self.assertIn('"developing"', js)
        self.assertIn('"collapse"', js)
        self.assertIn('"clear"', js)
        self.assertIn("aria-pressed", js)
        # The on state must be visibly distinct, in the palette already on the
        # board rather than a second visual language.
        self.assertRegex(css, r'\.judge button\[data-on="true"\][^}]*var\(--sev-critical\)')

    def test_judge_acknowledgement_does_not_borrow_detector_words(self):
        banned = ("detected", "warned", "watching", "incident")
        for stage in ("developing", "collapse", "clear"):
            text = acknowledgement(stage).lower()
            for word in banned:
                self.assertNotIn(word, text, stage)
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        judge = js[js.index("function renderJudge"):js.index("$(\"drawer-close\")")]
        for word in ("detected", "warned", "watching", "incident"):
            self.assertNotIn(word, judge.lower())

    def test_the_judge_control_never_upgrades_a_failure_into_a_claim(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        # The old dead adapter hard-coded its own success sentence; the toggle
        # must report the server's account of what actually happened.
        self.assertNotIn("Hidden incident fired. Detection will not be told", js)
        self.assertIn("body.message", js)
        self.assertIn("Nothing was injected", js)

    def test_investigation_outcome_is_shown_beside_lifecycle(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const outcome = investigation.outcome", js)
        self.assertIn('(outcome ? " · " + outcome : "")', js)

    def test_escalation_hypothesis_renders_statement_not_object_object(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("String(hypothesis)", js)
        self.assertIn("function c4FieldText", js)
        self.assertIn("value.statement", js)
        self.assertIn("value.explanation", js)
        self.assertIn("value.action", js)
        self.assertIn("No causal narrative is stored for this incident.", js)
        self.assertIn("payload.leading_hypothesis", js)
        self.assertIn("c4FieldText(", js)
        helper = js[js.index("function c4FieldText"):js.index("\n  function ", js.index("function c4FieldText"))]
        self.assertNotIn("JSON.stringify", helper)

        fallback = "No causal narrative is stored for this incident."

        def render(value):
            script = (
                helper
                + "\nprocess.stdout.write(c4FieldText("
                + json.dumps(value)
                + ", "
                + json.dumps(fallback)
                + "));\n"
            )
            return subprocess.check_output(["node", "-e", script], text=True)

        self.assertEqual(render({"statement": "Provider degradation"}), "Provider degradation")
        self.assertEqual(render(None), fallback)
        self.assertEqual(render("already text"), "already text")
        empty_statement = render({"statement": ""})
        self.assertEqual(empty_statement, fallback)
        self.assertNotIn("{", empty_statement)
        self.assertNotIn("}", empty_statement)
        self.assertEqual(render({"action": ""}), fallback)
        self.assertEqual(render({"explanation": ""}), fallback)



class EscalationEndpointTests(unittest.TestCase):
    """The escalation view is fed by stored rows, never by a UI reconstruction."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        os.environ.pop("CLEARWAVE_SLACK_WEBHOOK_URL", None)
        os.environ.pop("CLEARWAVE_SLACK_CHANNEL", None)
        self.app = SurfacesApp(self.db)

    def _seed(self, *incidents):
        connection = connect(self.db)
        self.addCleanup(connection.close)
        for incident in incidents:
            insert_incident(
                connection,
                incident,
                lifecycle_state=incident.get("lifecycle_state", "detected"),
            )
        return connection

    def test_binding_is_read_from_the_escalator_not_restated(self):
        self._seed(_incident("inc-1", "critical", "2026-08-29T10:00:00Z"))
        status, payload = self.app.handle("GET", "/api/escalations")
        self.assertEqual(status, 200)
        binding = {row["severity"]: tuple(row["channels"]) for row in payload["binding"]}
        for severity in ("low", "medium", "high", "critical"):
            self.assertEqual(binding[severity], channels_for(severity))

    def test_every_stored_incident_reports_its_channel_outcomes(self):
        connection = self._seed(
            _incident("inc-1", "critical", "2026-08-29T10:00:00Z"),
            _incident("inc-2", "low", "2026-08-29T10:05:00Z", merchant="merchant-b"),
        )
        persist_result(connection, "inc-1", {"incident_id": "inc-1"}, "diagnosed")
        persist_result(connection, "inc-2", {"incident_id": "inc-2"}, "diagnosed")
        _, payload = self.app.handle("GET", "/api/escalations")
        groups = {group["incident_id"]: group for group in payload["incidents"]}
        self.assertEqual(set(groups), {"inc-1", "inc-2"})
        critical = groups["inc-1"]
        self.assertEqual(tuple(critical["expected_channels"]), channels_for("critical"))
        self.assertEqual(
            {event["channel"] for event in critical["channels"]},
            set(channels_for("critical")),
        )
        self.assertEqual([event["channel"] for event in groups["inc-2"]["channels"]], ["dashboard"])

    def test_group_carries_the_stored_record_the_view_reads(self):
        connection = self._seed(_incident("inc-1", "critical", "2026-08-29T10:00:00Z"))
        persist_result(connection, "inc-1", {"incident_id": "inc-1"}, "diagnosed")
        _, payload = self.app.handle("GET", "/api/escalations")
        group = payload["incidents"][0]
        self.assertEqual(group["scope_label"], "merchant-a")
        self.assertEqual(group["blast_radius"]["attempted_payments"], 1000)
        self.assertEqual(group["change"]["actual"], 0.64)
        self.assertEqual(group["payload"]["incident_id"], "inc-1")
        self.assertEqual(payload["slack_channel"], DEFAULT_SLACK_CHANNEL)

    def test_detected_incident_does_not_escalate_before_a_diagnosis(self):
        self._seed(_incident("inc-1", "critical", "2026-08-29T10:00:00Z"))
        _, payload = self.app.handle("GET", "/api/escalations")
        group = payload["incidents"][0]
        self.assertEqual(group["channels"], [])
        self.assertEqual(group["payload"], {})

    def test_an_empty_store_answers_with_the_binding_and_no_incidents(self):
        _, payload = self.app.handle("GET", "/api/escalations")
        self.assertEqual(payload["incidents"], [])
        self.assertEqual(payload["calls"], [])
        self.assertEqual(len(payload["binding"]), 4)


class DashboardWiringTests(unittest.TestCase):
    """Every endpoint the server serves has to be read by the page."""

    def test_the_page_fetches_every_served_endpoint(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        for path in (
            "/api/overview",
            "/api/incidents",
            "/api/merchants",
            "/api/calls",
            "/api/escalations",
            "/api/trigger",
        ):
            self.assertIn(path, js, f"{path} is served but never fetched")

    def test_the_queue_reads_the_whole_queue_not_the_active_slice(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        # The queue used to be overview.incidents, which is active-only, so a
        # resolved incident could never appear in the incident queue at all.
        self.assertNotIn("state.queue = overview.incidents", js)
        self.assertIn("state.queue = payloads[1].incidents", js)

    def test_a_count_is_never_rendered_as_a_percentage(self):
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        # fmt() reads a bare 1 as 100%; blast-radius counts must not go through it.
        self.assertIn("function count(value)", js)
        self.assertIn("count(blast[key])", js)
        self.assertNotIn("fmt(blast[key])", js)

    def test_escalation_view_exists_and_is_read_only(self):
        html = (ROOT / "surfaces" / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "surfaces" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="escalation"', html)
        self.assertIn('id="escalation-board"', html)
        self.assertIn("Nothing here is a control", html)
        # No control may reach into a channel from this page.
        self.assertNotIn("/api/calls/", js)
        self.assertNotIn("acknowledge", js)

    def test_every_class_the_page_emits_has_a_rule_behind_it(self):
        static = ROOT / "surfaces" / "static"
        js = (static / "app.js").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")
        emitted = set()
        for source in (js, html):
            for match in re.finditer(r'class="([a-z][a-z0-9 _-]*)"', source):
                emitted.update(match.group(1).split())
        for match in re.finditer(r'className = "([a-z][a-z0-9 _-]*)"', js):
            emitted.update(match.group(1).split())
        styled = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
        # Containers the page only addresses by id carry no rule by design.
        containers = {"queue-board", "detail-board", "evidence-board", "escalation-board", "is-active"}
        self.assertEqual(sorted(emitted - styled - containers), [])


class EscalationRaceTests(unittest.TestCase):
    """Two overlapping HTTP requests must never fire real channels twice.

    surfaces/server.py runs ThreadingHTTPServer (one thread per connection),
    and overview()/queue()/detail() all call ensure_escalation() on every
    request. Without an atomic claim, two requests for the same brand-new
    critical incident - two browser tabs, or a poll overlapping a manual
    refresh - could both pass the "not yet escalated" check before either
    persists a row, posting to Slack and calling Twilio twice for one
    incident.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        seed = connect(self.db)
        insert_incident(seed, _incident("inc-race", "critical", "2026-08-29T10:00:00Z"))
        persist_result(seed, "inc-race", _diagnosis("inc-race"), "diagnosed", trail=[_trail_entry()])
        seed.close()

    def test_ensure_escalation_fires_channels_exactly_once_under_concurrent_calls(self):
        call_count = 0
        count_lock = threading.Lock()

        def fake_escalate(incident, result, **kwargs):
            nonlocal call_count
            with count_lock:
                call_count += 1
            return [{"channel": "dashboard", "status": "delivered", "payload": {}}]

        start_barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker():
            connection = connect(self.db)
            try:
                incident = load_incident(connection, "inc-race")
                investigation = load_investigation(connection, "inc-race")
                start_barrier.wait(timeout=5)  # maximise overlap at the claim INSERT
                ensure_escalation(connection, incident, investigation)
            except BaseException as exc:  # noqa: BLE001 - surface it to the main thread
                errors.append(exc)
            finally:
                connection.close()

        with patch("surfaces.store.escalate", side_effect=fake_escalate):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(call_count, 1, "escalate() must fire exactly once, not once per racing request")
        final_connection = connect(self.db)
        self.addCleanup(final_connection.close)
        self.assertEqual(len(load_escalation(final_connection, "inc-race")), 1)

    def test_repeat_call_after_resolution_never_calls_escalate_again(self):
        connection = connect(self.db)
        self.addCleanup(connection.close)
        incident = load_incident(connection, "inc-race")
        investigation = load_investigation(connection, "inc-race")
        first = ensure_escalation(connection, incident, investigation)
        self.assertTrue(first)

        def failing_escalate(*args, **kwargs):
            raise AssertionError("escalate() must not be called again for an already-resolved incident")

        with patch("surfaces.store.escalate", side_effect=failing_escalate):
            second = ensure_escalation(connection, incident, investigation)
        self.assertEqual(second, first)

    def test_loser_returns_without_firing_and_a_later_read_sees_the_winners_result(self):
        # Simulates the loser side of the race directly: a claim already
        # exists (the winner is mid-flight), so this call must not fire
        # escalate() and must not raise - it returns whatever is stored,
        # which may still be empty, and self-heals on the next read once the
        # winner finishes.
        connection = connect(self.db)
        self.addCleanup(connection.close)
        with connection:
            connection.execute(
                "INSERT INTO escalation_claim (incident_id, claimed_at) VALUES (?, ?)",
                ("inc-race", "2026-08-29T10:00:00.000Z"),
            )

        def failing_escalate(*args, **kwargs):
            raise AssertionError("the losing caller must never fire escalate()")

        incident = load_incident(connection, "inc-race")
        investigation = load_investigation(connection, "inc-race")
        with patch("surfaces.store.escalate", side_effect=failing_escalate):
            outcome = ensure_escalation(connection, incident, investigation)
        self.assertEqual(outcome, [])  # nothing persisted yet - the "winner" never actually ran

        # The winner finishes and persists its rows; a later read now sees them.
        with connection:
            connection.execute(
                """INSERT INTO escalation_event
                   (incident_id, channel, status, payload, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("inc-race", "dashboard", "delivered", "{}", None, "2026-08-29T10:00:01.000Z"),
            )
        healed = ensure_escalation(connection, incident, investigation)
        self.assertEqual(len(healed), 1)


class ServerHardeningTests(unittest.TestCase):
    """Defensive handling for a server reachable during a live judge demo."""

    def setUp(self):
        os.environ["CLEARWAVE_SURFACES_QUIET"] = "1"
        self.addCleanup(os.environ.pop, "CLEARWAVE_SURFACES_QUIET", None)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        self.httpd = make_server(self.db, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(_stop_server, self.httpd, self.thread)
        self.port = self.httpd.server_address[1]

    def _raw_post(self, path, body, content_length_header):
        """Send a raw POST request to the test server and return its response.
        
        Parameters:
        	path (str): The request path.
        	body (bytes): The request body.
        	content_length_header (str): The value of the Content-Length header.
        
        Returns:
        	http.client.HTTPResponse: The server response.
        """
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.putrequest("POST", path, skip_accept_encoding=True)
            connection.putheader("Content-Length", content_length_header)
            connection.endheaders()
            connection.send(body)
            return connection.getresponse()
        finally:
            connection.close()

    def test_do_post_rejects_a_non_numeric_content_length(self):
        response = self._raw_post("/api/trigger", b"{}", "not-a-number")
        self.assertEqual(response.status, 400)

    def test_do_post_rejects_a_negative_content_length(self):
        response = self._raw_post("/api/trigger", b"", "-1")
        self.assertEqual(response.status, 400)

    def test_do_post_rejects_a_content_length_over_the_body_cap(self):
        from surfaces.server import MAX_BODY_BYTES

        response = self._raw_post("/api/trigger", b"", str(MAX_BODY_BYTES + 1))
        self.assertEqual(response.status, 400)

    def test_internal_exception_returns_a_clean_500_not_a_broken_connection(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch.object(SurfacesApp, "overview", side_effect=RuntimeError("boom")):
            try:
                opener.open(f"http://127.0.0.1:{self.port}/api/overview", timeout=2)
                self.fail("expected an HTTPError with status 500")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 500)
                payload = json.loads(exc.read().decode("utf-8"))
                self.assertIn("error", payload)
                # No stack trace or internal detail leaks into the response body.
                self.assertNotIn("boom", json.dumps(payload))

    def test_static_traversal_guard_rejects_a_sibling_directory_sharing_a_prefix(self):
        from surfaces.server import STATIC_DIR

        sibling = STATIC_DIR.parent / (STATIC_DIR.name + "-evil")
        sibling.mkdir(exist_ok=True)
        self.addCleanup(lambda: sibling.rmdir())
        secret = sibling / "secret.txt"
        secret.write_text("should never be served", encoding="utf-8")
        self.addCleanup(secret.unlink)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(f"http://127.0.0.1:{self.port}/../{sibling.name}/secret.txt", timeout=2)
            self.fail("expected a 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == "__main__":
    unittest.main()


class WatchRailTests(unittest.TestCase):
    """A watch must read as a quieter rail, never as an active incident.

    `INACTIVE_STATES` was `{"resolved", "mitigated"}` and `_is_active` treated
    everything else as active, so `watching` - which is neither - counted as an
    active incident: it inflated the "Right now" business figures and dropped
    into the incident queue styled like a crossed floor.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        seed = connect(self.db)
        insert_incident(seed, _incident("inc-live", "critical", "2026-08-29T10:00:00Z"))
        insert_incident(seed, _watch("inc-watch"), lifecycle_state="watching")
        seed.close()
        self.app = SurfacesApp(self.db)

    def test_a_watch_is_never_counted_as_an_active_incident(self):
        overview = self.app.overview()
        self.assertEqual(overview["active_incident_count"], 1)
        self.assertEqual(overview["source_incident_id"], "inc-live")
        self.assertEqual(
            [item["incident_id"] for item in overview["incidents"]], ["inc-live"]
        )

    def test_a_watch_never_inflates_an_overview_figure(self):
        overview = self.app.overview()
        # Every headline figure is copied off the one active incident, so the
        # watch cannot have contributed to any of them.
        self.assertEqual(overview["gmv"], {"amount": 100000.0, "currency": "USD"})
        self.assertEqual(
            overview["financial_impact"]["loss_per_hour"],
            {"amount": 112000.0, "currency": "USD"},
        )
        self.assertEqual(
            [row["merchant_id"] for row in overview["merchant_health"]], ["merchant-a"]
        )

    def test_a_watch_is_not_in_the_incident_queue_but_is_on_the_rail(self):
        queue = self.app.queue()
        self.assertEqual([item["incident_id"] for item in queue["incidents"]], ["inc-live"])
        self.assertEqual([item["incident_id"] for item in queue["watches"]], ["inc-watch"])
        self.assertEqual(
            [item["incident_id"] for item in self.app.overview()["watches"]], ["inc-watch"]
        )

    def test_a_watch_is_not_a_row_on_the_escalation_view(self):
        # It fires no channel by construction, so listing it there would read as
        # an incident that failed to escalate.
        groups = self.app.escalations()["incidents"]
        self.assertEqual([group["incident_id"] for group in groups], ["inc-live"])

    def test_the_rail_carries_the_projected_figure_and_both_floor_vectors(self):
        watch = self.app.queue()["watches"][0]
        self.assertEqual(watch["lifecycle_state"], "watching")
        self.assertEqual(watch["severity"], "low")
        # Projected, under its own stored key, never merged into loss_per_hour.
        self.assertEqual(watch["projected_loss_per_hour"]["amount"], 15798.36)
        self.assertIn("not money already lost", watch["projected_loss_per_hour"]["basis"])
        # The reason it is not yet an incident: the detection floor it has not
        # crossed, the watch predicate that did hold, and the trajectory.
        self.assertFalse(watch["detection_floors"]["z_min"])
        self.assertTrue(watch["watch_floors"]["worsening"])
        self.assertEqual(watch["trajectory"], 1)
        self.assertEqual(watch["reasons"], ["conversion_near_miss"])
        self.assertEqual(watch["scope_label"], "merchant-w")

    def test_present_recomputes_nothing_for_the_rail(self):
        stored = _watch("inc-copy")
        item = present.watch_item(stored)
        self.assertEqual(
            item["projected_loss_per_hour"], stored["financial_impact"]["projected_loss_per_hour"]
        )
        self.assertEqual(item["detection_floors"], stored["detection"]["detection_floors"])
        self.assertEqual(item["statement"], stored["detection"]["watch"]["statement"])


class WatchNeverPagesTests(unittest.TestCase):
    """The no-paging guarantee must be structural, not a chain of conventions.

    Before this, a watch could not page only because `ensure_escalation` gates
    on a C4 result and the investigation daemon claims `detected`, so a watch
    never gets one. Nothing checked the lifecycle state. These tests point
    `ensure_escalation` at a watch that does have a result - the mistake derek's
    constraint names - and require silence.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db = Path(self._tmpdir.name) / "clearwave.db"
        self.connection = connect(self.db)
        self.addCleanup(self.connection.close)
        self.fired = []

        def spy(incident, result, **kwargs):
            self.fired.append(incident.get("incident_id"))
            return [{"channel": "slack", "status": "delivered", "payload": {}}]

        patcher = patch("surfaces.store.escalate", spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ensure_escalation_refuses_a_watch_that_has_an_investigation_result(self):
        insert_incident(self.connection, _watch("inc-watch"), lifecycle_state="watching")
        persist_result(
            self.connection, "inc-watch", _diagnosis("inc-watch"), "diagnosed", trail=[_trail_entry()]
        )
        stored = load_incident(self.connection, "inc-watch")
        self.assertEqual(stored["lifecycle_state"], "watching")

        outcomes = ensure_escalation(
            self.connection, stored, load_investigation(self.connection, "inc-watch")
        )

        self.assertEqual(outcomes, [])
        self.assertEqual(self.fired, [], "no channel may fire for a watch")
        self.assertEqual(load_escalation(self.connection, "inc-watch"), [])
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM escalation_claim WHERE incident_id = 'inc-watch'"
        ).fetchone()[0]
        self.assertEqual(rows, 0, "a watch is refused before anything is claimed")

    def test_the_lifecycle_state_is_what_refuses_it(self):
        # The same row, the same result, differing only in lifecycle state. If
        # the guard were removed the first call would fire and this test would
        # fail on the watch, not on the incident.
        for incident_id, state, expected in (("inc-watch", "watching", []), ("inc-real", "detected", ["inc-real"])):
            record = _watch(incident_id)
            record["lifecycle_state"] = state
            insert_incident(self.connection, record, lifecycle_state=state)
            persist_result(
                self.connection, incident_id, _diagnosis(incident_id), "diagnosed", trail=[_trail_entry()]
            )
            ensure_escalation(
                self.connection,
                load_incident(self.connection, incident_id),
                load_investigation(self.connection, incident_id),
            )
        self.assertEqual(self.fired, ["inc-real"])

    def test_only_detected_and_beyond_may_escalate(self):
        self.assertNotIn("watching", ESCALATABLE_STATES)
        self.assertEqual(
            ESCALATABLE_STATES,
            frozenset(
                {"detected", "investigating", "diagnosed", "acknowledged", "mitigated", "resolved"}
            ),
        )

    def test_a_dashboard_read_of_a_store_holding_a_watch_pages_nothing(self):
        insert_incident(self.connection, _watch("inc-watch"), lifecycle_state="watching")
        persist_result(
            self.connection, "inc-watch", _diagnosis("inc-watch"), "diagnosed", trail=[_trail_entry()]
        )
        app = SurfacesApp(self.db)
        app.overview()
        app.queue()
        app.detail("inc-watch")
        self.assertEqual(self.fired, [])


class WatchRailPageTests(unittest.TestCase):
    """The rail must be drawn, quiet, and never in a severity colour."""

    def setUp(self):
        static = ROOT / "surfaces" / "static"
        self.html = (static / "index.html").read_text(encoding="utf-8")
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_the_rail_has_its_own_region_apart_from_the_incident_queue(self):
        self.assertIn('id="overview-watch-rail"', self.html)
        self.assertIn('id="queue-watch-rail"', self.html)
        # It is never appended into the queue table itself.
        self.assertIn("renderWatchRail", self.js)
        render_queue = self.js.split("function renderQueue")[1].split("// The warning rail.")[0]
        self.assertNotIn('class="rail', render_queue)
        self.assertNotIn("state.watches", render_queue)

    def test_the_badge_says_watching_and_is_not_a_severity(self):
        self.assertIn('class="watching"', self.js)
        self.assertIn(">watching<", self.js)
        self.assertRegex(self.css, r"\.watching\s*\{")
        # No severity badge markup is reused for a watch.
        rail_js = self.js.split("function watchRow")[1].split("function onsetLine")[0]
        self.assertNotIn("badgePair", rail_js)
        self.assertNotIn("severityClass", rail_js)

    def test_no_rule_on_the_rail_reaches_for_a_critical_colour(self):
        start = self.css.index("/* The warning rail.")
        rail_css = self.css[start:]
        for banned in ("--sev-critical", "--sev-high", "--sev-medium"):
            self.assertNotIn(banned, rail_css)

    def test_the_projection_is_worded_as_a_projection(self):
        self.assertIn("if this continues", self.js)
        self.assertIn("projected_loss_per_hour", self.js)
        # "loss" alone would present a projection as a realised figure.
        self.assertNotIn("Loss / hour", self.js)

    def test_the_rail_computes_nothing(self):
        rail_js = self.js.split("// The warning rail.")[1].split("function readoutCell")[0]
        for arithmetic in ("* 100", " / 60", "reduce(", "+ Number("):
            self.assertNotIn(arithmetic, rail_js)

    def test_the_empty_rail_is_deliberate_rather_than_half_drawn(self):
        self.assertIn("Nothing is being watched", self.js)
        self.assertRegex(self.css, r"\.rail\.is-quiet")
