"""Offline tests for the deterministic L4 investigation core."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from investigation.agent import InvestigationRun
from investigation.gateway import EvidenceGateway
from investigation.prefilter import compute_signature, prefilter
from investigation.degrade import degrade_result
from investigation.runner import InvestigationRunner
from investigation.store import (
    CLAIM_LEASE_SECONDS,
    append_trail_entry,
    claim_incident,
    connect,
    evidence_fingerprint,
    insert_incident,
    model_call_summary,
    next_result_version,
    persist_result,
    read_result,
    reclaim_expired_claims,
)
from investigation.trail import EvidenceTrail
from surfaces.present import detail as present_detail
from surfaces.store import ESCALATABLE_STATES, connect as surfaces_connect, ensure_escalation, load_investigation


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
        gateway = EvidenceGateway(query_budget=0, runner=lambda tool, parameters, timeout: {"ok": True})
        bundle = gateway.run_opening({"cohort_metrics": REQUEST})
        self.assertNotIn("error", bundle["cohort_metrics"])
        self.assertEqual(gateway.remaining_budget, 0)
        response = gateway.call("cohort_metrics", {"cohort": {"merchant_id": "merchant-b"}})
        self.assertEqual(response["error"]["code"], "budget_exceeded")

    def test_budget_refuses_gracefully_after_limit(self):
        gateway = EvidenceGateway(query_budget=1, runner=lambda tool, parameters, timeout: {"ok": True})
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

    def test_claiming_a_watch_succeeds_once(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(
            connection,
            {"incident_id": "inc-watch", "affected_cohort": {}, "severity": "low"},
            lifecycle_state="watching",
        )
        self.assertTrue(claim_incident(connection, "inc-watch"))
        self.assertFalse(claim_incident(connection, "inc-watch"))
        state = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
        ).fetchone()[0]
        # A watch stays watching while claimed so the board and the verifier
        # still see a preventive near-miss, not an incident.
        self.assertEqual(state, "watching")

    def test_persisting_a_watch_result_restores_watching(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(
            connection,
            {"incident_id": "inc-watch", "affected_cohort": {}, "severity": "low"},
            lifecycle_state="watching",
        )
        self.assertTrue(claim_incident(connection, "inc-watch"))
        persist_result(
            connection,
            "inc-watch",
            {"incident_id": "inc-watch"},
            "insufficient_evidence",
            resume_state="watching",
            evidence_fingerprint="fp-1",
        )
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )
        self.assertEqual(model_call_summary(connection)["total"], 1)

    def test_persisting_without_resume_leaves_a_watch_unpaged(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(
            connection,
            {"incident_id": "inc-watch", "affected_cohort": {}, "severity": "low"},
            lifecycle_state="watching",
        )
        persist_result(connection, "inc-watch", {"incident_id": "inc-watch"}, "diagnosed")
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )


class PreventiveRunnerTests(unittest.TestCase):
    def _watch(self, incident_id="inc-watch", actual=0.84):
        return {
            "incident_id": incident_id,
            "affected_cohort": {"provider": "adyen"},
            "change": {
                "metric": "payment_approval_conversion",
                "expected": 0.92,
                "actual": actual,
                "absolute_delta": round(actual - 0.92, 4),
            },
            "onset": "2026-08-30T10:00:00Z",
            "persistence": {
                "is_persistent": False,
                "observed_for_seconds": 120,
                "last_observed_at": "2026-08-30T10:02:00Z",
            },
            "blast_radius": {"attempted_payments": 80, "affected_providers": 1},
            "financial_impact": {
                "projected_loss_per_hour": {"amount": 400.0, "currency": "USD"},
                "loss_per_hour": {"amount": 12.0, "currency": "USD"},
            },
            "severity": "low",
            "lifecycle_state": "watching",
            "detection": {
                "watch": {
                    "reasons": ["conversion_near_miss"],
                    "degraded_leading_indicators": [],
                    "leading_indicators": {"mean_latency_ms": {"ratio": 1.1}},
                }
            },
        }

    def test_runner_investigates_a_watch_and_leaves_it_watching(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")

        class Agent:
            def investigate(self, incident):
                return degrade_result(incident, reason="watch agent")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runs = runner.poll_once(wait=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].claimed_from, "watching")
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )
        stored = read_result(connection, "inc-watch")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["result"]["diagnostic_confidence"], "low")
        self.assertEqual(runner.model_calls, 1)
        self.assertEqual(model_call_summary(connection)["total"], 1)

    def test_unchanged_watch_is_not_reinvestigated(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")
        calls = []

        class Agent:
            def investigate(self, incident):
                calls.append(incident["incident_id"])
                return degrade_result(incident, reason="once")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        self.assertEqual(len(runner.poll_once(wait=True)), 1)
        self.assertEqual(len(runner.poll_once(wait=True)), 0)
        self.assertEqual(calls, ["inc-watch"])
        self.assertEqual(runner.model_calls, 1)

    def test_meaningful_change_reinvestigates_the_same_record(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch(actual=0.84)
        insert_incident(connection, watch, lifecycle_state="watching")
        calls = []

        class Agent:
            def investigate(self, incident):
                calls.append(incident["change"]["actual"])
                return degrade_result(incident, reason="refresh")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runner.poll_once(wait=True)
        worse = self._watch(actual=0.70)
        connection.execute(
            "UPDATE incident SET record = ? WHERE incident_id = ?",
            (json.dumps(worse, sort_keys=True), "inc-watch"),
        )
        connection.commit()
        runs = runner.poll_once(wait=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(calls, [0.84, 0.70])
        self.assertEqual(runner.model_calls, 2)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM investigation_result WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )

    def test_crossing_the_floors_enriches_the_same_record(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")

        class Agent:
            def investigate(self, incident):
                return degrade_result(incident, reason=str(incident.get("lifecycle_state")))

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runner.poll_once(wait=True)
        detected = dict(watch)
        detected["lifecycle_state"] = "detected"
        detected["severity"] = "high"
        connection.execute(
            "UPDATE incident SET record = ?, lifecycle_state = 'detected', severity = 'high' "
            "WHERE incident_id = ?",
            (json.dumps(detected, sort_keys=True), "inc-watch"),
        )
        connection.commit()
        runs = runner.poll_once(wait=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].claimed_from, "detected")
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "diagnosed",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM investigation_result WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(model_call_summary(connection)["by_incident"][0]["incident_id"], "inc-watch")
        self.assertEqual(model_call_summary(connection)["total"], 2)

    def test_a_watch_with_a_result_still_does_not_page(self):
        connection = surfaces_connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")

        class Agent:
            def investigate(self, incident):
                return degrade_result(incident, reason="watch agent")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runner.poll_once(wait=True)
        row = connection.execute(
            "SELECT record, lifecycle_state, severity FROM incident WHERE incident_id = 'inc-watch'"
        ).fetchone()
        record = json.loads(row["record"])
        record["lifecycle_state"] = row["lifecycle_state"]
        self.assertEqual(row["lifecycle_state"], "watching")
        self.assertNotIn("watching", ESCALATABLE_STATES)
        events = ensure_escalation(connection, record, load_investigation(connection, "inc-watch"))
        self.assertEqual(events, [])

    def test_runner_prepares_a_detector_opened_store(self):
        from detector import store as detector_store

        connection = detector_store.connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        detector_store.save_incident(connection, watch, lifecycle_state="watching")

        class Agent:
            def investigate(self, incident):
                return degrade_result(incident, reason="detector store")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runs = runner.poll_once(wait=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )

    def test_abandoned_investigating_claim_is_reclaimed(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")
        self.assertTrue(claim_incident(connection, "inc-watch"))
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )
        connection.execute(
            "UPDATE investigation_claim SET claimed_at = '2000-01-01T00:00:00.000Z' "
            "WHERE incident_id = 'inc-watch'"
        )
        connection.commit()

        class Agent:
            def investigate(self, incident):
                return degrade_result(incident, reason="recovered claim")

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runs = runner.poll_once(wait=True)
        self.assertEqual(len(runs), 1)
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )
        self.assertIsNotNone(read_result(connection, "inc-watch"))

    def test_fresh_claim_is_not_reclaimed_before_the_lease_expires(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")
        self.assertTrue(claim_incident(connection, "inc-watch"))
        self.assertEqual(reclaim_expired_claims(connection), [])
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )
        self.assertGreaterEqual(CLAIM_LEASE_SECONDS, 300)

    def test_investigating_without_a_lease_is_reclaimed_immediately(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        watch = self._watch()
        insert_incident(connection, watch, lifecycle_state="watching")
        connection.execute(
            "UPDATE incident SET lifecycle_state = 'investigating' WHERE incident_id = 'inc-watch'"
        )
        connection.commit()
        self.assertEqual(reclaim_expired_claims(connection), ["inc-watch"])
        self.assertEqual(
            connection.execute(
                "SELECT lifecycle_state FROM incident WHERE incident_id = 'inc-watch'"
            ).fetchone()[0],
            "watching",
        )

    def test_fingerprint_ignores_persistence_timers(self):
        first = self._watch()
        second = self._watch()
        second["persistence"] = {
            "is_persistent": False,
            "observed_for_seconds": 9999,
            "last_observed_at": "2026-08-30T12:00:00Z",
        }
        second["onset"] = "2026-08-30T09:00:00Z"
        self.assertEqual(evidence_fingerprint(first), evidence_fingerprint(second))


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


def _trail_identity(entries):
    return [
        (
            entry.get("sequence"),
            entry.get("query_id"),
            entry.get("tool"),
            entry.get("outcome"),
            bool(entry.get("executed", True)),
        )
        for entry in entries
    ]


class LiveTrailPersistenceTests(unittest.TestCase):
    def _store(self):
        connection = connect(":memory:")
        self.addCleanup(connection.close)
        insert_incident(connection, {"incident_id": "inc-1", "affected_cohort": {}})
        return connection

    def _persist_live(self, connection, incident_id, version):
        def persist_entry(entry):
            append_trail_entry(connection, incident_id, entry, version=version)

        return persist_entry

    def _gateway(self, connection, version, incident_id="inc-1"):
        return EvidenceGateway(
            runner=lambda tool, parameters, timeout: {"ok": True, "tool": tool},
            persist_entry=self._persist_live(connection, incident_id, version),
        )

    def test_entries_are_readable_after_each_gateway_call(self):
        connection = self._store()
        self.assertTrue(claim_incident(connection, "inc-1"))
        version = next_result_version(connection, "inc-1")
        gateway = self._gateway(connection, version)
        gateway.call("cohort_metrics", REQUEST, opening=True)
        live = read_result(connection, "inc-1")
        self.assertIsNone(live["completed_at"])
        self.assertEqual(len(live["trail"]), 1)
        self.assertEqual(live["trail"][0]["sequence"], 1)
        self.assertEqual(live["trail"][0]["tool"], "cohort_metrics")
        gateway.call("drilldown", {"incident_id": "inc-1"}, opening=True)
        live = read_result(connection, "inc-1")
        self.assertEqual([entry["tool"] for entry in live["trail"]], ["cohort_metrics", "drilldown"])
        self.assertEqual([entry["sequence"] for entry in live["trail"]], [1, 2])

    def test_final_write_over_live_entries_does_not_duplicate(self):
        connection = self._store()
        self.assertTrue(claim_incident(connection, "inc-1"))
        version = next_result_version(connection, "inc-1")
        gateway = self._gateway(connection, version)
        gateway.call("cohort_metrics", REQUEST, opening=True)
        gateway.call("drilldown", {"incident_id": "inc-1"}, opening=True)
        produced = list(gateway.trail.entries)
        stored = persist_result(
            connection,
            "inc-1",
            {"incident_id": "inc-1"},
            "diagnosed",
            version=version,
            trail=gateway.trail,
        )
        self.assertEqual(stored["version"], version)
        self.assertEqual(_trail_identity(stored["trail"]), _trail_identity(produced))
        count = connection.execute(
            "SELECT COUNT(*) FROM evidence_trail WHERE incident_id = 'inc-1'"
        ).fetchone()[0]
        self.assertEqual(count, len(produced))
        self.assertEqual([entry["sequence"] for entry in stored["trail"]], [1, 2])

    def test_reinvestigation_live_entries_use_the_new_version(self):
        connection = self._store()
        first = {
            "query_id": "q_old",
            "tool": "cohort_metrics",
            "parameters": REQUEST,
            "response": {"query_id": "q_old"},
            "timestamp": "2026-08-29T10:00:00Z",
            "duration_ms": 1.0,
            "outcome": "success",
            "executed": True,
            "sequence": 1,
        }
        persist_result(
            connection,
            "inc-1",
            {"incident_id": "inc-1", "run": 1},
            "diagnosed",
            trail=[first],
        )
        connection.execute(
            "UPDATE incident SET lifecycle_state = 'detected' WHERE incident_id = 'inc-1'"
        )
        connection.commit()
        self.assertTrue(claim_incident(connection, "inc-1"))
        version = next_result_version(connection, "inc-1")
        self.assertEqual(version, 2)
        gateway = self._gateway(connection, version)
        gateway.call("decline_breakdown", REQUEST, opening=True)
        live = read_result(connection, "inc-1")
        self.assertEqual(live["version"], 2)
        self.assertIsNone(live["completed_at"])
        self.assertEqual([entry["tool"] for entry in live["trail"]], ["decline_breakdown"])
        self.assertEqual(live["trail"][0]["sequence"], 1)
        previous = read_result(connection, "inc-1", version=1)
        self.assertEqual(previous["result"], {"incident_id": "inc-1", "run": 1})
        self.assertEqual(previous["trail"][0]["query_id"], "q_old")
        stored = persist_result(
            connection,
            "inc-1",
            {"incident_id": "inc-1", "run": 2},
            "diagnosed",
            version=version,
            trail=gateway.trail,
        )
        self.assertEqual(stored["version"], 2)
        self.assertEqual(_trail_identity(stored["trail"]), _trail_identity(gateway.trail.entries))
        versions = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT result_version, COUNT(*) FROM evidence_trail "
                "WHERE incident_id = 'inc-1' GROUP BY result_version"
            ).fetchall()
        }
        self.assertEqual(versions, {1: 1, 2: 1})

    def test_raising_live_write_does_not_fail_the_run(self):
        def boom(entry):
            raise RuntimeError("store unavailable")

        gateway = EvidenceGateway(
            runner=lambda tool, parameters, timeout: {"ok": True},
            persist_entry=boom,
        )
        response = gateway.call("cohort_metrics", REQUEST, opening=True)
        self.assertNotIn("error", response)
        self.assertEqual(len(gateway.trail.entries), 1)
        connection = self._store()
        stored = persist_result(
            connection,
            "inc-1",
            {"incident_id": "inc-1"},
            "diagnosed",
            trail=gateway.trail,
        )
        self.assertEqual(len(stored["trail"]), 1)
        self.assertEqual(stored["trail"][0]["query_id"], gateway.trail.entries[0]["query_id"])

    def test_in_flight_result_does_not_page(self):
        connection = surfaces_connect(":memory:")
        self.addCleanup(connection.close)
        incident = {
            "incident_id": "inc-1",
            "affected_cohort": {},
            "severity": "critical",
            "lifecycle_state": "investigating",
        }
        insert_incident(connection, incident, lifecycle_state="detected")
        self.assertTrue(claim_incident(connection, "inc-1"))
        version = next_result_version(connection, "inc-1")
        gateway = self._gateway(connection, version)
        gateway.call("cohort_metrics", REQUEST, opening=True)
        live = read_result(connection, "inc-1")
        self.assertFalse(bool(live))
        events = ensure_escalation(connection, incident, live)
        self.assertEqual(events, [])

    def test_present_serves_claimed_at_as_started_at_while_in_flight(self):
        connection = self._store()
        incident = {"incident_id": "inc-1", "affected_cohort": {}, "lifecycle_state": "investigating"}
        self.assertTrue(claim_incident(connection, "inc-1"))
        claimed_at = connection.execute(
            "SELECT claimed_at FROM investigation_claim WHERE incident_id = 'inc-1'"
        ).fetchone()[0]
        version = next_result_version(connection, "inc-1")
        gateway = self._gateway(connection, version)
        gateway.call("cohort_metrics", REQUEST, opening=True)
        live = read_result(connection, "inc-1")
        payload = present_detail(incident, live)
        self.assertEqual(payload["investigation"]["started_at"], claimed_at)
        self.assertIsNone(payload["investigation"]["completed_at"])
        self.assertFalse(payload["investigation"]["narrative_available"])
        self.assertEqual(len(payload["evidence_trail"]), 1)
        self.assertEqual(payload["evidence_trail"][0]["sequence"], 1)

    def test_runner_live_and_final_share_version_and_sequence(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        connection = connect(path)
        self.addCleanup(connection.close)
        insert_incident(connection, {"incident_id": "inc-1", "affected_cohort": {}})
        first_query = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        class Agent:
            query_budget = 6

            def investigate(self, incident, gateway=None):
                gw = gateway or EvidenceGateway(
                    query_budget=self.query_budget,
                    runner=lambda tool, parameters, timeout: {"ok": True, "tool": tool},
                )
                gw.call("cohort_metrics", REQUEST, opening=True)
                first_query.set()
                if not release.wait(5):
                    raise RuntimeError("timed out waiting to continue investigation")
                gw.call("drilldown", {"incident_id": incident["incident_id"]}, opening=True)
                run = InvestigationRun(
                    result=degrade_result(incident, reason="live runner"),
                    trail=gw.trail,
                    started_at="2026-08-30T12:00:00.000Z",
                    completed_at="2026-08-30T12:01:00.000Z",
                    duration_ms=1.0,
                )
                finished.set()
                return run

        runner = InvestigationRunner(connection, Agent())
        self.addCleanup(runner.close)
        runner.poll_once(wait=False)
        self.assertTrue(first_query.wait(5))
        peek = connect(path)
        self.addCleanup(peek.close)
        live = read_result(peek, "inc-1")
        self.assertIsNone(live["completed_at"])
        self.assertEqual(len(live["trail"]), 1)
        self.assertEqual(live["trail"][0]["sequence"], 1)
        self.assertEqual(live["trail"][0]["tool"], "cohort_metrics")
        in_flight_version = live["version"]
        release.set()
        self.assertTrue(finished.wait(5))
        runs = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            runs = runner.poll_once(wait=False)
            if runs:
                break
            time.sleep(0.02)
        self.assertEqual(len(runs), 1)
        stored = read_result(connection, "inc-1")
        self.assertEqual(stored["version"], in_flight_version)
        self.assertEqual(_trail_identity(stored["trail"]), _trail_identity(runs[0].trail.entries))
        count = connection.execute(
            "SELECT COUNT(*) FROM evidence_trail WHERE incident_id = 'inc-1'"
        ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual([entry["sequence"] for entry in stored["trail"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
