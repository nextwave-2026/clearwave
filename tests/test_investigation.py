"""Offline tests for the deterministic L4 investigation core."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from investigation.gateway import EvidenceGateway
from investigation.prefilter import compute_signature, prefilter
from investigation.store import append_trail_entry, claim_incident, connect, insert_incident, persist_result, read_result
from investigation.trail import EvidenceTrail


ROOT = Path(__file__).resolve().parents[1]
WINDOW = {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}
REQUEST = {"cohort": {"merchant_id": "merchant-a"}, "window": WINDOW}


class GatewayTests(unittest.TestCase):
    def test_refuses_tool_outside_allowlist_without_attempting_it(self):
        calls = []

        def runner(tool, parameters, timeout):
            calls.append(tool)
            return {"ok": True}

        gateway = EvidenceGateway(runner=runner)
        response = gateway.call("shell", REQUEST)
        self.assertEqual(response["error"]["code"], "tool_not_allowed")
        self.assertEqual(calls, [])
        self.assertFalse(gateway.verify_citation(response["query_id"]))
        self.assertEqual(gateway.trail.entries[0]["outcome"], "refused")

    def test_identical_parameters_have_stable_query_id(self):
        gateway = EvidenceGateway()
        first = gateway.call("cohort_metrics", {"window": WINDOW, "cohort": {"merchant_id": "merchant-a"}})
        second = gateway.call("cohort_metrics", {"cohort": {"merchant_id": "merchant-a"}, "window": WINDOW})
        self.assertEqual(first["query_id"], second["query_id"])

    def test_records_every_call_including_failure(self):
        def runner(tool, parameters, timeout):
            return 1, json.dumps({"error": {"code": "fixture_failure", "message": "failed"}})

        trail = EvidenceTrail()
        gateway = EvidenceGateway(runner=runner, trail=trail)
        response = gateway.call("drilldown", {"incident_id": "inc-1"})
        self.assertEqual(response["error"]["code"], "fixture_failure")
        self.assertEqual(len(trail.entries), 1)
        self.assertEqual(trail.entries[0]["outcome"], "failure")
        self.assertTrue(gateway.verify_citation(response["query_id"]))
        rendered = trail.render()
        self.assertIn("drilldown", rendered)
        self.assertIn("fixture_failure", rendered)

    def test_opening_set_does_not_consume_further_call_budget(self):
        gateway = EvidenceGateway(query_budget=0)
        bundle = gateway.run_opening({"cohort_metrics": REQUEST})
        self.assertNotIn("error", bundle["cohort_metrics"])
        self.assertEqual(gateway.remaining_budget, 0)
        response = gateway.call("cohort_metrics", {"cohort": {"merchant_id": "merchant-b"}})
        self.assertEqual(response["error"]["code"], "budget_exceeded")

    def test_budget_refuses_gracefully_after_limit(self):
        gateway = EvidenceGateway(query_budget=1)
        first = gateway.call("cohort_metrics", REQUEST)
        second = gateway.call("cohort_metrics", {"cohort": {"merchant_id": "merchant-b"}, "window": WINDOW})
        self.assertNotIn("error", first)
        self.assertEqual(second["error"]["code"], "budget_exceeded")
        self.assertEqual(gateway.additional_calls, 1)
        self.assertEqual(len(gateway.trail.entries), 2)
        self.assertFalse(gateway.verify_citation(second["query_id"]))

    def test_external_unavailable_is_success(self):
        gateway = EvidenceGateway(tool_dir=ROOT / "stubs" / "evidence")
        response = gateway.call("external_status", {"provider": "provider-p2"})
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(gateway.trail.entries[0]["outcome"], "success")

    def test_unknown_citation_is_false(self):
        gateway = EvidenceGateway(runner=lambda tool, parameters, timeout: {"ok": True})
        gateway.call("cohort_metrics", REQUEST)
        self.assertFalse(gateway.verify_citation("q_never_executed"))

    def test_accepts_metric_series_and_still_refuses_unknown_names(self):
        calls = []

        def runner(tool, parameters, timeout):
            calls.append(tool)
            return {"ok": True}

        gateway = EvidenceGateway(runner=runner)
        accepted = gateway.call("metric_series", {"cohort": {}, "window": WINDOW})
        self.assertNotIn("error", accepted)
        self.assertTrue(accepted["query_id"].startswith("q_metric_series_"))
        self.assertEqual(calls, ["metric_series"])
        self.assertTrue(gateway.verify_citation(accepted["query_id"]))
        refused = gateway.call("not_a_real_tool", REQUEST)
        self.assertEqual(refused["error"]["code"], "tool_not_allowed")
        self.assertEqual(calls, ["metric_series"])
        self.assertFalse(gateway.verify_citation(refused["query_id"]))


class StoreTests(unittest.TestCase):
    def test_claiming_an_incident_twice_only_succeeds_once(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(connection, {"incident_id": "inc-1", "affected_cohort": {}, "severity": "high"})
        self.assertTrue(claim_incident(connection, "inc-1"))
        self.assertFalse(claim_incident(connection, "inc-1"))
        state = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-1'"
        ).fetchone()[0]
        self.assertEqual(state, "investigating")

    def test_persists_result_and_full_trail(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(connection, {"incident_id": "inc-1", "affected_cohort": {}})
        entry = {
            "query_id": "q_one",
            "tool": "cohort_metrics",
            "parameters": REQUEST,
            "response": {"query_id": "q_one", "ok": True},
            "timestamp": "2026-08-29T10:00:00Z",
            "duration_ms": 4.5,
            "outcome": "success",
            "executed": True,
        }
        append_trail_entry(connection, "inc-1", entry)
        stored = persist_result(connection, "inc-1", {"incident_id": "inc-1"}, "diagnosed", trail=[entry])
        loaded = read_result(connection, "inc-1")
        self.assertEqual(stored["version"], 1)
        self.assertEqual(len(loaded["trail"]), 1)
        self.assertEqual(loaded["trail"][0]["query_id"], "q_one")
        self.assertEqual(loaded["result"], {"incident_id": "inc-1"})
        self.assertEqual(
            connection.execute("SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-1'").fetchone()[0],
            "diagnosed",
        )


class PrefilterTests(unittest.TestCase):
    def test_materially_different_signatures_produce_different_candidates(self):
        incident = {
            "affected_cohort": {
                "merchant_id": "merchant-a",
                "provider": "provider-p2",
                "payment_method": "card",
                "country": "CO",
            },
            "change": {"expected": 0.92, "actual": 0.64},
            "blast_radius": {"affected_merchants": 1, "affected_countries": 1, "affected_providers": 1},
        }
        provider = {
            "cohort_metrics": {
                "payment_metrics": {"approval_conversion": 0.64, "expected_approval_conversion": 0.92},
                "attempt_metrics": {"approval_conversion": 0.47},
                "baseline": {"attempt_approval_conversion": 0.90},
            },
            "cohort_compare": {"target": {"payment_metrics": {"approval_conversion": 0.64}}, "siblings": [{"payment_metrics": {"approval_conversion": 0.92}}]},
            "decline_breakdown": {"reasons": [{"reason": "timeout", "shift": 0.60}, {"reason": "issuer_decline", "shift": -0.30}]},
            "retry_stats": {"retry_amplification_factor": 1.35, "queue": {"depth_start": 10, "depth_end": 40, "depth_peak": 50}},
            "operational_metrics": {"timeout_rate": 0.35, "error_rate": 0.01, "latency_ms": {"p95": 1800, "p99": 4200}, "service_health": {"status": "degraded"}, "runtime_health": {"status": "healthy"}},
            "confounding_check": {"structurally_inseparable": False},
        }
        issuer = {
            "cohort_metrics": {
                "payment_metrics": {"approval_conversion": 0.64, "expected_approval_conversion": 0.92},
                "attempt_metrics": {"approval_conversion": 0.64},
                "baseline": {"attempt_approval_conversion": 0.90},
            },
            "cohort_compare": {"target": {"payment_metrics": {"approval_conversion": 0.64}}, "siblings": [{"payment_metrics": {"approval_conversion": 0.92}}]},
            "decline_breakdown": {"reasons": [{"reason": "issuer_decline", "shift": 0.55}, {"reason": "timeout", "shift": -0.05}]},
            "retry_stats": {"retry_amplification_factor": 1.0, "queue": {"depth_start": 10, "depth_end": 10, "depth_peak": 10}},
            "operational_metrics": {"timeout_rate": 0.02, "error_rate": 0.01, "latency_ms": {"p95": 300, "p99": 600}, "service_health": {"status": "healthy"}, "runtime_health": {"status": "healthy"}},
            "confounding_check": {"structurally_inseparable": True},
        }
        provider_names = {candidate["name"] for candidate in prefilter(incident, provider)["candidates"]}
        issuer_names = {candidate["name"] for candidate in prefilter(incident, issuer)["candidates"]}
        self.assertIn("provider_degradation", provider_names)
        self.assertIn("issuer_over_decline", issuer_names)
        self.assertNotEqual(provider_names, issuer_names)

    def test_detector_blast_radius_spellings_match_contract_scope(self):
        def scope(radius):
            incident = {"affected_cohort": {"provider": "provider-p2"}, "blast_radius": radius}
            return compute_signature(incident, {})["affected_cohort"]

        contract_radius = {
            "affected_merchants": 2,
            "affected_countries": 1,
            "affected_card_networks": 1,
            "affected_providers": 1,
            "affected_payment_methods": 2,
            "affected_issuing_banks": 1,
        }
        detector_radius = {
            "affected_merchant_ids": 2,
            "affected_countrys": 1,
            "affected_card_networks": 1,
            "affected_providers": 1,
            "affected_payment_methods": 2,
            "affected_issuing_banks": 1,
        }

        self.assertEqual(scope(detector_radius), scope(contract_radius))
        self.assertEqual(scope(contract_radius)["scope"], "broad")
        self.assertEqual(scope(contract_radius)["width"], 2)
        both_spellings = {
            **contract_radius,
            "affected_merchant_ids": 99,
            "affected_countrys": 99,
        }
        self.assertEqual(scope(both_spellings), scope(contract_radius))

    def test_empty_observations_have_a_reason_and_nonempty_fallback(self):
        result = prefilter({}, {})
        self.assertTrue(result["candidates"])
        self.assertTrue(result["reason"])
        self.assertIn("unknown_observable_failure", {candidate["name"] for candidate in result["candidates"]})


if __name__ == "__main__":
    unittest.main()
