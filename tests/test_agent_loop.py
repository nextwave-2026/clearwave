"""Offline tests for the bounded L4 agent loop."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from investigation.agent import InvestigationAgent
from investigation.contracts import InvestigationResult
from investigation.gateway import EvidenceGateway
from investigation.ledger import HypothesisLedger, LedgerError
from investigation.prefilter import prefilter
from investigation.prompt import assemble_prompt
from investigation.runner import InvestigationRunner
from investigation.store import connect, insert_incident


WINDOW = {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}
INCIDENT = {
    "incident_id": "inc-test-1",
    "affected_cohort": {
        "merchant_id": "merchant-a",
        "provider": "provider-p2",
        "country": "CO",
        "issuing_bank": "bank-x",
    },
    "change": {"metric": "payment_approval_conversion", "expected": 0.92, "actual": 0.64},
    "onset": WINDOW["start"],
    "persistence": {"is_persistent": True, "observed_for_seconds": 900, "last_observed_at": WINDOW["end"]},
    "blast_radius": {"attempted_payments": 1000, "affected_merchants": 1, "affected_countries": 1, "affected_providers": 1},
    "financial_impact": {"gmv_at_risk": {"amount": 28000, "currency": "USD"}},
    "severity": "critical",
    "lifecycle_state": "detected",
}


class FakeClient:
    def __init__(self, callback):
        self.responses = SimpleNamespace(create=callback)
        self.calls = []


def evidence_runner(tool, parameters, timeout, *, confounded=False):
    responses = {
        "cohort_metrics": {
            "payment_metrics": {"approval_conversion": 0.64, "expected_approval_conversion": 0.92},
            "attempt_metrics": {"approval_conversion": 0.47},
        },
        "cohort_compare": {
            "target": {"payment_metrics": {"approval_conversion": 0.64}},
            "siblings": [{"payment_metrics": {"approval_conversion": 0.92}}],
        },
        "decline_breakdown": {"reasons": [{"reason": "timeout", "shift": 0.6}, {"reason": "issuer_decline", "shift": -0.3}]},
        "retry_stats": {"retry_amplification_factor": 1.35, "queue": {"depth_start": 10, "depth_end": 40, "depth_peak": 50}},
        "operational_metrics": {
            "timeout_rate": 0.35,
            "error_rate": 0.01,
            "latency_ms": {"p95": 1800, "p99": 4200},
            "service_health": {"status": "degraded"},
            "runtime_health": {"status": "healthy"},
        },
        "confounding_check": {
            "structurally_inseparable": confounded,
            "cross_tabulation": {"dimensions": ["provider", "issuing_bank"], "rows": []},
        },
        "financial_impact": {"gmv_at_risk": {"amount": 28000, "currency": "USD"}},
        "incident_history": {"incidents": []},
    }
    return responses.get(tool, {})


def gateway_for(*, confounded=False) -> EvidenceGateway:
    return EvidenceGateway(
        runner=lambda tool, parameters, timeout: evidence_runner(
            tool, parameters, timeout, confounded=confounded
        ),
        query_budget=2,
    )


def valid_result(gateway: EvidenceGateway, *, competing: bool = False):
    entry = gateway.trail.entries[0]
    citation = {"claim": "The opening evidence was observed.", "query_id": entry["query_id"], "tool": entry["tool"]}
    result = {
        "incident_id": INCIDENT["incident_id"],
        "confirmed_facts": [{"statement": "The affected cohort changed.", "evidence": [citation]}],
        "leading_hypothesis": {"statement": "Provider degradation is the leading explanation.", "evidence": [citation]},
        "supporting_evidence": [citation],
        "competing_explanations": ([{"explanation": "Issuer over-decline remains possible.", "evidence": [citation]}] if competing else []),
        "why_ambiguity_exists": {"statement": "The opening confounding check cannot separate provider and issuer.", "evidence": [citation]},
        "missing_evidence": [{"request": "Compare the issuer through another provider.", "reason": "That observation would discriminate the causes.", "evidence": [citation]}],
        "diagnostic_confidence": "medium",
        "recommended_next_action": {"action": "Investigate the provider before changing routing.", "urgency": "now", "basis": [citation]},
        "outcome": "ambiguous" if competing else "diagnosed",
    }
    return result


class AgentLoopTests(unittest.TestCase):
    def test_loop_stops_at_turn_budget(self):
        gateway = gateway_for()
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if kwargs.get("tools"):
                return {"output": [{"type": "function_call", "call_id": "c1", "name": "incident_history", "arguments": "{}"}]}
            return {"output_text": json.dumps(valid_result(gateway))}

        run = InvestigationAgent(FakeClient(create), max_turns=2, timeout_seconds=2).investigate(INCIDENT, gateway)
        self.assertEqual(run.outcome, "diagnosed")
        self.assertEqual(sum(bool(call.get("tools")) for call in calls), 2)
        self.assertEqual(len(calls), 3)

    def test_fabricated_citation_triggers_one_retry(self):
        gateway = gateway_for()
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if kwargs.get("text") and len(calls) == 2:
                payload = valid_result(gateway)
                payload["supporting_evidence"][0]["query_id"] = "q_never_ran"
                return {"output_text": json.dumps(payload)}
            return {"output_text": json.dumps(valid_result(gateway))}

        run = InvestigationAgent(FakeClient(create), max_turns=1, timeout_seconds=2).investigate(INCIDENT, gateway)
        self.assertEqual(run.outcome, "diagnosed")
        self.assertEqual(len(calls), 3)
        self.assertTrue(any("q_never_ran" in str(call.get("input")) for call in calls[2:]))

    def test_second_validation_failure_degrades(self):
        gateway = gateway_for()
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return {"output_text": json.dumps({"incident_id": INCIDENT["incident_id"]})}

        run = InvestigationAgent(FakeClient(create), max_turns=1, timeout_seconds=2).investigate(INCIDENT, gateway)
        self.assertEqual(run.outcome, "agent_unavailable")
        self.assertIn("deterministic incident facts", run["confirmed_facts"][0]["statement"].lower())

    def test_wall_clock_timeout_degrades_without_waiting(self):
        gateway = gateway_for()

        def create(**kwargs):
            time.sleep(1)
            return {"output_text": "{}"}

        started = time.monotonic()
        run = InvestigationAgent(FakeClient(create), timeout_seconds=0.03).investigate(INCIDENT, gateway)
        elapsed = time.monotonic() - started
        self.assertEqual(run.outcome, "agent_unavailable")
        self.assertLess(elapsed, 0.5)

    def test_no_severity_field_can_appear_in_result(self):
        gateway = gateway_for()
        gateway.opening_bundle(INCIDENT)
        payload = valid_result(gateway)
        payload["severity"] = "critical"
        with self.assertRaises(ValidationError):
            InvestigationResult.model_validate(payload)

    def test_confounded_case_keeps_competing_explanation(self):
        gateway = gateway_for(confounded=True)

        def create(**kwargs):
            return {"output_text": json.dumps(valid_result(gateway, competing=True))}

        run = InvestigationAgent(FakeClient(create), timeout_seconds=2).investigate(INCIDENT, gateway)
        self.assertEqual(run.outcome, "ambiguous")
        self.assertEqual(len(run["competing_explanations"]), 1)
        self.assertIn("issuer", run["competing_explanations"][0]["explanation"].lower())

    def test_prompt_excludes_run_identifiers_and_quarantined_material(self):
        incident = {**INCIDENT, "scenario_id": "provider-secret", "hidden_truth": {"cause": "provider"}}
        prompt = assemble_prompt(incident, {}, [{"hypothesis": "provider_degradation"}])
        self.assertNotIn("scenario_id", prompt)
        self.assertNotIn("provider-secret", prompt)
        self.assertNotIn("hidden_truth", prompt)
        self.assertNotIn("evaluator", prompt.lower())


class RunnerTests(unittest.TestCase):
    def test_runner_claims_persists_and_completes_incident(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(connection, INCIDENT)

        class Agent:
            def investigate(self, incident):
                return {"incident_id": incident["incident_id"], "outcome": "agent_unavailable"}

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runs = runner.run_once()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].outcome, "agent_unavailable")
        self.assertEqual(
            connection.execute("SELECT lifecycle_state FROM incident").fetchone()[0],
            "diagnosed",
        )
        self.assertEqual(
            connection.execute("SELECT outcome FROM investigation_result").fetchone()[0],
            "agent_unavailable",
        )


class LedgerTests(unittest.TestCase):
    def test_contradiction_requires_executed_contradicting_citation(self):
        gateway = gateway_for()
        opening = gateway.opening_bundle(INCIDENT)
        ledger = HypothesisLedger.from_prefilter(prefilter(INCIDENT, opening), trail=gateway.trail)
        hypothesis = ledger.entries[0].hypothesis
        with self.assertRaises(LedgerError):
            ledger.mark_contradicted(hypothesis, ["q_never_ran"])
        with self.assertRaises(LedgerError):
            ledger.mark_contradicted(hypothesis, [gateway.trail.entries[0]["query_id"]])


if __name__ == "__main__":
    unittest.main()
