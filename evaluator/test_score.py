#!/usr/bin/env python3
"""Standard-library tests for the ClearWave evaluator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from score import score_diagnosis, score_rankings


ROOT = Path(__file__).resolve().parents[1]


def truth(
    scenario_id: str,
    cohort: dict[str, str],
    *,
    confounded: bool = False,
    priority_relations: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario_id,
        "injected": {
            "scenario_id": scenario_id,
            "scenario_name": scenario_id,
            "affected_cohort": cohort,
            "failure_mode": "test_failure",
            "strength": {
                "metric": "payment_approval_conversion",
                "baseline": 0.92,
                "target": 0.64,
                "unit": "ratio",
                "direction": "decrease",
            },
            "start_time": "2026-08-29T10:00:00Z",
            "end_time": "2026-08-29T10:15:00Z",
            "event_time_bucket_seconds": 900,
        },
        "observed": {"affected_cohorts": [], "aggregate_magnitude": {}},
        "evaluation": {
            "confounded": confounded,
            "priority_relations": priority_relations or [],
        },
    }


def diagnosis(
    cohort: dict[str, str],
    *,
    confidence: str = "high",
    competing: list[dict[str, str]] | None = None,
    missing: list[dict[str, str]] | None = None,
    priority_rank: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "diagnosed_cohort": cohort,
        "investigation_result": {
            "leading_hypothesis": {"statement": "The observed route is the leading hypothesis."},
            "competing_explanations": competing or [],
            "missing_evidence": missing or [],
            "diagnostic_confidence": confidence,
        },
    }
    if priority_rank is not None:
        value["priority_rank"] = priority_rank
    return value


class ScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_path = ROOT / "stubs" / "fixtures" / "cohort_metrics.json"
        self.fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.fixture_cohort = self.fixture["response"]["cohort"]

    def test_fixture_cohort_exact_match_has_full_precision_and_recall(self) -> None:
        hidden = truth("provider-issuer-confounded", self.fixture_cohort, confounded=True)
        hidden["observed"] = {
            "affected_cohorts": [
                {
                    "relationship": "direct",
                    "cohort": self.fixture_cohort,
                    "magnitude": self.fixture["response"]["payment_metrics"],
                }
            ],
            "aggregate_magnitude": self.fixture["response"]["payment_metrics"],
        }
        result = score_diagnosis(
            diagnosis(
                self.fixture_cohort,
                confidence="medium",
                competing=[{"explanation": "Bank X over-decline remains possible."}],
                missing=[{"request": "Compare Bank X through another provider."}],
            ),
            hidden,
        )
        cohort = result["components"]["cohort_localisation"]
        self.assertEqual(cohort["precision"], 1.0)
        self.assertEqual(cohort["recall"], 1.0)
        self.assertEqual(cohort["matched_dimensions"], sorted(self.fixture_cohort))
        self.assertTrue(result["passed"])

    def test_broad_or_wrong_cohort_reports_lower_scores_and_dimension_names(self) -> None:
        hidden = truth("fine-grained-combination", self.fixture_cohort)
        broad = score_diagnosis(diagnosis({"merchant_id": "merchant-a"}), hidden)
        broad_cohort = broad["components"]["cohort_localisation"]
        self.assertEqual(broad_cohort["precision"], 1.0)
        self.assertLess(broad_cohort["recall"], 1.0)
        self.assertIn("provider", broad_cohort["missing_dimensions"])

        wrong = dict(self.fixture_cohort)
        wrong["provider"] = "provider-p3"
        wrong_result = score_diagnosis(diagnosis(wrong), hidden)
        wrong_cohort = wrong_result["components"]["cohort_localisation"]
        self.assertLess(wrong_cohort["precision"], 1.0)
        self.assertLess(wrong_cohort["recall"], 1.0)
        self.assertIn("provider", wrong_cohort["missing_dimensions"])
        self.assertIn("provider", wrong_cohort["spurious_dimensions"])
        self.assertEqual(wrong_cohort["mismatched_dimensions"][0]["dimension"], "provider")

    def test_confounded_confident_single_cause_fails(self) -> None:
        hidden = truth(
            "provider-issuer-confounded",
            {"provider": "provider-p2", "issuing_bank": "bank-x"},
            confounded=True,
        )
        result = score_diagnosis(
            diagnosis({"provider": "provider-p2", "issuing_bank": "bank-x"}), hidden
        )
        uncertainty = result["components"]["uncertainty_handling"]
        self.assertFalse(uncertainty["passed"])
        self.assertIn("named_competing_explanation", uncertainty["failure_reasons"])
        self.assertEqual(result["verdict"], "fail")

    def test_confounded_hedged_answer_passes(self) -> None:
        hidden = truth(
            "provider-issuer-confounded",
            {"provider": "provider-p2", "issuing_bank": "bank-x"},
            confounded=True,
        )
        result = score_diagnosis(
            diagnosis(
                {"provider": "provider-p2", "issuing_bank": "bank-x"},
                confidence="medium",
                competing=[{"explanation": "Bank X over-decline remains possible."}],
                missing=[
                    {
                        "request": "Compare P2 traffic from another issuer or Bank X through another provider.",
                        "reason": "The current mapping is structurally inseparable.",
                    }
                ],
            ),
            hidden,
        )
        self.assertTrue(result["components"]["uncertainty_handling"]["passed"])
        self.assertEqual(result["verdict"], "pass")

    def test_high_impact_scenario_outranks_dramatic_low_volume_scenario(self) -> None:
        high_truth = truth(
            "high-impact-small-percentage",
            {"merchant_id": "merchant-a"},
            priority_relations=[
                {
                    "relation": "outranks",
                    "scenario_id": "dramatic-low-volume-anomaly",
                }
            ],
        )
        low_truth = truth("dramatic-low-volume-anomaly", {"merchant_id": "merchant-d"})
        result = score_rankings(
            [
                {
                    "diagnosis": diagnosis({"merchant_id": "merchant-a"}, priority_rank=20),
                    "hidden_truth": high_truth,
                },
                {
                    "diagnosis": diagnosis({"merchant_id": "merchant-d"}, priority_rank=80),
                    "hidden_truth": low_truth,
                },
            ]
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["ordering_checks"][0]["passed"])
        self.assertTrue(result["scores"][0]["components"]["severity_ordering"]["passed"])
        self.assertFalse(result["ordering_checks"][0]["uses_absolute_severity"])

        reversed_result = score_rankings(
            [
                {
                    "diagnosis": diagnosis({"merchant_id": "merchant-a"}, priority_rank=80),
                    "hidden_truth": high_truth,
                },
                {
                    "diagnosis": diagnosis({"merchant_id": "merchant-d"}, priority_rank=20),
                    "hidden_truth": low_truth,
                },
            ]
        )
        self.assertFalse(reversed_result["passed"])


if __name__ == "__main__":
    unittest.main()
