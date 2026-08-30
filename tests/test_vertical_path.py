"""End-to-end proof that seed, detect, and investigate share one store."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from detector import config, evidence, store
from detector.cli import _sweep
from tests import synthetic

from investigation.contracts import InvestigationResult
from investigation.gateway import EvidenceGateway
from investigation.store import connect as investigation_connect
from investigation.vertical import (
    citations_from,
    citations_verify_against_trail,
    execute_investigation_only,
    execute_vertical_path,
    seed_and_detect,
)
from investigation.vertical import main as vertical_main


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


class FinancialConsistencyTests(unittest.TestCase):
    """The cited C2 impact must use the window that produced C3."""

    def test_gateway_financial_impact_matches_generated_incident(self):
        root = Path(__file__).resolve().parents[1]
        for events in (synthetic.confounded_incident(), synthetic.with_provider_incident()):
            with self.subTest(event_count=len(events)):
                with tempfile.TemporaryDirectory() as directory:
                    db = Path(directory) / "clearwave.db"
                    connection = store.connect(db)
                    try:
                        store.ingest(connection, events)
                        sweep = _sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
                        incident = sweep["incident"]
                        self.assertIsNotNone(incident)
                        with patch.dict(os.environ, {"CLEARWAVE_DB": str(db)}):
                            cited = EvidenceGateway(
                                cwd=root,
                                python_executable=sys.executable,
                            ).opening_bundle(incident)["financial_impact"]
                    finally:
                        connection.close()

                detection_window = incident["detection"]["window"]
                expected_window = {
                    "start": evidence.schema.iso_utc(detection_window["start_epoch"]),
                    "end": evidence.schema.iso_utc(detection_window["end_epoch"]),
                }
                self.assertEqual(cited["window"]["start"], expected_window["start"])
                self.assertEqual(cited["window"]["end"], expected_window["end"])
                for field in (
                    "attempted_value",
                    "estimated_lost_approved_volume",
                    "gmv_at_risk",
                    "loss_per_hour",
                ):
                    self.assertEqual(cited[field], incident["financial_impact"][field])


if __name__ == "__main__":
    unittest.main()


class InvestigateOnlyTests(unittest.TestCase):
    """A prepared store is investigated in place: no reset, no reseed, no second detect."""

    def setUp(self):
        self._saved_key = os.environ.pop("OPENAI_API_KEY", None)
        self._saved_db = os.environ.get("CLEARWAVE_DB")
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(self._restore_env)
        self.db = Path(self._dir.name) / "prepared.db"
        self.detected = seed_and_detect(self.db)
        self.assertTrue(self.detected)

    def _restore_env(self):
        if self._saved_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._saved_key
        if self._saved_db is None:
            os.environ.pop("CLEARWAVE_DB", None)
        else:
            os.environ["CLEARWAVE_DB"] = self._saved_db

    def _event_count(self):
        connection = investigation_connect(self.db)
        try:
            return connection.execute("SELECT count(*) AS n FROM attempt").fetchone()["n"]
        finally:
            connection.close()

    def test_investigates_the_stored_incident_without_reseeding(self):
        before = self._event_count()
        incident_id = str(self.detected[0]["incident_id"])
        outcome = execute_investigation_only(self.db, use_model=False)
        self.assertEqual(outcome.path, "investigate-only")
        self.assertEqual(outcome.incident.get("incident_id"), incident_id)
        self.assertEqual(outcome.lifecycle_after_detect, "detected")
        self.assertEqual(outcome.lifecycle_after_investigate, "diagnosed")
        self.assertEqual(self._event_count(), before, "investigate-only must not reseed the store")

    def test_a_named_incident_id_is_the_one_investigated(self):
        incident_id = str(self.detected[0]["incident_id"])
        outcome = execute_investigation_only(self.db, incident_id=incident_id, use_model=False)
        self.assertEqual(outcome.result["incident_id"], incident_id)
        InvestigationResult.model_validate(outcome.result)

    def test_an_unknown_incident_id_is_refused_by_name(self):
        with self.assertRaises(RuntimeError) as raised:
            execute_investigation_only(self.db, incident_id="inc-does-not-exist", use_model=False)
        self.assertIn("inc-does-not-exist", str(raised.exception))

    def test_a_store_with_nothing_detected_says_so_instead_of_seeding(self):
        execute_investigation_only(self.db, use_model=False)
        with self.assertRaises(RuntimeError) as raised:
            execute_investigation_only(self.db, use_model=False)
        self.assertIn("lifecycle_state detected", str(raised.exception))

    def test_a_missing_store_is_never_created_by_investigate_only(self):
        absent = Path(self._dir.name) / "absent.db"
        with self.assertRaises(RuntimeError):
            execute_investigation_only(absent, use_model=False)
        self.assertFalse(absent.exists())

    def test_the_cli_flag_investigates_without_seeding(self):
        before = self._event_count()
        code = vertical_main(["--db", str(self.db), "--investigate-only"])
        self.assertEqual(code, 0)
        self.assertEqual(self._event_count(), before)
