"""Unit tests for the demo-chain verifier helpers.

These do not start a stack. The live command is `make verify-demo`.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_demo as verify  # noqa: E402


class MoneyAndCohort(unittest.TestCase):
    def test_money_amount_reads_stored_shape(self):
        self.assertEqual(verify.money_amount({"amount": 19784.62, "currency": "USD"}), 19784.62)
        self.assertIsNone(verify.money_amount("not money"))
        self.assertEqual(verify.money_text({"amount": 10, "currency": "USD"}), "USD 10.00")

    def test_injected_cohort_matches_merchant_or_provider(self):
        self.assertTrue(
            verify.is_injected_cohort({"affected_cohort": {"provider": "adyen"}})
        )
        self.assertTrue(
            verify.is_injected_cohort({"affected_cohort": {"merchant_id": "merchant-b"}})
        )
        self.assertFalse(
            verify.is_injected_cohort({"affected_cohort": {"merchant_id": "merchant-a", "provider": "dlocal"}})
        )


class BoardTruth(unittest.TestCase):
    def test_watch_in_incident_list_is_a_lie(self):
        lied, evidence = verify.watch_presented_as_active(
            {
                "active_incident_count": 1,
                "incidents": [{"incident_id": "inc-1", "lifecycle_state": "watching"}],
                "watches": [{"incident_id": "inc-1", "lifecycle_state": "watching"}],
            }
        )
        self.assertTrue(lied)
        self.assertIn("inc-1", evidence)

    def test_watch_beside_an_incident_is_honest(self):
        lied, _ = verify.watch_presented_as_active(
            {
                "active_incident_count": 1,
                "incidents": [{"incident_id": "inc-2", "lifecycle_state": "diagnosed"}],
                "watches": [{"incident_id": "inc-1", "lifecycle_state": "watching"}],
            }
        )
        self.assertFalse(lied)

    def test_count_mismatch_is_a_lie(self):
        lied, _ = verify.watch_presented_as_active(
            {
                "active_incident_count": 1,
                "incidents": [],
                "watches": [{"incident_id": "inc-1", "lifecycle_state": "watching"}],
            }
        )
        self.assertTrue(lied)

    def test_revenue_heading(self):
        self.assertTrue(verify.page_leads_with_revenue("<h2>Revenue impact</h2>"))
        self.assertFalse(verify.page_leads_with_revenue("<h2>Right now</h2>"))


class StaleRevenue(unittest.TestCase):
    JS_BUG = (
        "renderMerchants(state.merchants);\n"
        "if (!headline) {\n"
        '  overviewBoard.innerHTML = "<h3>No revenue at risk</h3>";\n'
        "}\n"
    )
    JS_CLEARS = (
        "if (!headline) {\n"
        "  overviewMerchants.innerHTML = '';\n"
        '  overviewBoard.innerHTML = "<p>No incidents in the store.</p>";\n'
        "  return;\n"
        "}\n"
    )

    def test_calm_headline_with_merchant_money_is_a_contradiction(self):
        contradiction, evidence = verify.stale_revenue_on_board(
            {"active_incident_count": 0, "incidents": []},
            [
                {
                    "merchant_id": "merchant-b",
                    "source_incident_id": "inc-resolved",
                    "active_incident_count": 0,
                    "financial_impact": {
                        "loss_per_hour": {"amount": 19784.62, "currency": "USD"},
                        "gmv_at_risk": {"amount": 1648.72, "currency": "USD"},
                    },
                }
            ],
            self.JS_BUG,
        )
        self.assertTrue(contradiction)
        self.assertIn("19784.62", evidence)

    def test_no_contradiction_when_page_clears_merchant_rows(self):
        contradiction, _ = verify.stale_revenue_on_board(
            {"active_incident_count": 0, "incidents": []},
            [
                {
                    "merchant_id": "merchant-b",
                    "financial_impact": {
                        "loss_per_hour": {"amount": 19784.62, "currency": "USD"},
                    },
                }
            ],
            self.JS_CLEARS,
        )
        self.assertFalse(contradiction)


class TableAndHonesty(unittest.TestCase):
    def test_overall_fail_when_any_beat_fails(self):
        beats = [
            verify.Beat("a", "A", True, "ok"),
            verify.Beat("b", "B", False, "broken row count=17"),
        ]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            verify.print_table(beats)
        text = buffer.getvalue()
        self.assertIn("FAIL", text)
        self.assertIn("OVERALL FAIL", text)
        self.assertIn("broken row count=17", text)
        self.assertIn("1/2 beats passed", text)

    def test_labels_do_not_claim_seasonal_awareness(self):
        titles = " ".join(
            [
                "Clean start is genuinely warm",
                "Healthy traffic stays quiet",
                "Stage one developing deviation",
                "No page before detection",
                "One cohort, one record",
                "Stage two collapse",
                "Clear returns the board to healthy",
                "The board leads with revenue impact and does not count a watch as an active incident",
                "The board never shows money that is not currently at risk",
            ]
        ).lower()
        for word in verify.FORBIDDEN_CLAIM_WORDS:
            self.assertNotIn(word, titles)

    def test_paging_counts_slack_and_phone_only(self):
        effects = verify.paging_side_effects(
            {
                "incidents": [
                    {
                        "incident_id": "inc-1",
                        "channels": [
                            {"channel": "dashboard", "status": "delivered"},
                            {"channel": "slack", "status": "not_configured"},
                            {"channel": "phone", "status": "not_configured"},
                        ],
                    }
                ]
            },
            {"calls": [{"incident_id": "inc-1"}]},
        )
        self.assertEqual(effects["slack_count"], 1)
        self.assertEqual(effects["phone_count"], 1)
        self.assertEqual(effects["pending_call_count"], 1)


class IsolatedStackGuard(unittest.TestCase):
    def test_default_project_is_not_the_live_demo(self):
        self.assertEqual(verify.DEFAULT_PROJECT, "clearwave-verify-demo")
        self.assertNotIn(verify.DEFAULT_PROJECT, verify.FORBIDDEN_PROJECTS)
        self.assertNotIn(verify.DEFAULT_SURFACES_PORT, verify.OCCUPIED_PORTS)


if __name__ == "__main__":
    unittest.main()
