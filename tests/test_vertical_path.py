"""End-to-end proof that seed, detect, and investigate share one store."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from investigation.contracts import InvestigationResult
from investigation.gateway import EvidenceGateway
from investigation.vertical import (
    citations_from,
    citations_verify_against_trail,
    execute_vertical_path,
)


class VerticalPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_key = os.environ.pop("OPENAI_API_KEY", None)
        cls._saved_db = os.environ.get("CLEARWAVE_DB")
        cls._dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._dir.cleanup)
        cls.db = Path(cls._dir.name) / "clearwave.db"
        cls.outcome = execute_vertical_path(cls.db, recreate=True, use_model=False)

    @classmethod
    def tearDownClass(cls):
        if cls._saved_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = cls._saved_key
        if cls._saved_db is None:
            os.environ.pop("CLEARWAVE_DB", None)
        else:
            os.environ["CLEARWAVE_DB"] = cls._saved_db

    def test_seeding_then_detecting_produces_a_detected_incident(self):
        self.assertTrue(self.outcome.detected_incidents)
        self.assertEqual(self.outcome.lifecycle_after_detect, "detected")
        self.assertTrue(self.outcome.incident.get("incident_id"))

    def test_runner_claims_the_incident_and_moves_it_to_diagnosed(self):
        self.assertEqual(self.outcome.lifecycle_after_investigate, "diagnosed")
        self.assertEqual(self.outcome.incident.get("incident_id"), self.outcome.result["incident_id"])

    def test_produced_result_validates_against_the_c4_contract(self):
        validated = InvestigationResult.model_validate(self.outcome.result)
        self.assertEqual(validated.incident_id, self.outcome.result["incident_id"])
        self.assertNotIn("severity", self.outcome.result)

    def test_every_citation_verifies_against_the_gateway_trail(self):
        citations = list(citations_from(self.outcome.result))
        self.assertTrue(citations, "degrade result must cite executed opening evidence")
        missing = citations_verify_against_trail(self.outcome.result, self.outcome.trail)
        self.assertEqual(missing, [])
        executed = {entry["query_id"] for entry in self.outcome.trail if entry.get("executed")}
        for citation in citations:
            self.assertIn(citation["query_id"], executed)

    def test_without_an_api_key_the_outcome_is_agent_unavailable(self):
        self.assertFalse(self.outcome.api_key_present)
        self.assertEqual(self.outcome.mode, "agent_unavailable")
        self.assertEqual(self.outcome.outcome, "agent_unavailable")
        self.assertNotEqual(self.outcome.mode, "model")

    def test_degraded_incident_still_carries_localisation_money_and_trail(self):
        cohort = self.outcome.incident.get("affected_cohort") or {}
        money = self.outcome.incident.get("financial_impact") or {}
        self.assertTrue(cohort, "incident must still name a localised cohort")
        self.assertIn("provider", cohort)
        gmv = money.get("gmv_at_risk") or {}
        self.assertGreater(float(gmv.get("amount") or 0), 0)
        executed = [entry for entry in self.outcome.trail if entry.get("executed")]
        self.assertGreaterEqual(len(executed), 7)
        self.assertTrue(self.outcome.result.get("supporting_evidence"))

    def test_the_same_store_measured_twice_produces_the_same_query_identifiers(self):
        incident = self.outcome.incident
        first = EvidenceGateway().opening_bundle(incident)
        second = EvidenceGateway().opening_bundle(incident)
        ids_first = {name: response["query_id"] for name, response in first.items()}
        ids_second = {name: response["query_id"] for name, response in second.items()}
        self.assertEqual(ids_first, ids_second)
        window = {
            "start": incident["onset"],
            "end": (incident.get("persistence") or {}).get("last_observed_at") or incident["onset"],
        }
        parameters = {"cohort": incident.get("affected_cohort") or {}, "window": window}
        series_first = EvidenceGateway().call("metric_series", parameters)
        series_second = EvidenceGateway().call("metric_series", parameters)
        self.assertNotIn("error", series_first)
        self.assertEqual(series_first["query_id"], series_second["query_id"])
        self.assertTrue(series_first["query_id"].startswith("q_metric_series_"))


if __name__ == "__main__":
    unittest.main()
