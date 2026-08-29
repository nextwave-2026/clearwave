#!/usr/bin/env python3
"""Run the offline Clearwave contract slice from canonical fixture data."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "stubs" / "fixtures"
EVIDENCE = ROOT / "stubs" / "evidence"
WINDOW = {
    "start": "2026-08-29T10:00:00Z",
    "end": "2026-08-29T10:15:00Z",
}
COHORT = {
    "merchant_id": "merchant-a",
    "provider": "provider-p2",
    "payment_method": "card",
    "card_network": "mastercard",
    "country": "CO",
    "issuing_bank": "bank-x",
}
INCIDENT_ID = "inc-2026-08-29-001"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object in {path}")
    return value


def stage(title: str) -> None:
    print(f"\n=== STAGE {title} ===")


def run_tool(name: str, request: dict[str, Any]) -> dict[str, Any]:
    command = [sys.executable, str(EVIDENCE / f"{name}.py")]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} returned invalid JSON: {exc}") from exc
    if completed.returncode != 0 or "error" in response:
        raise RuntimeError(f"{name} failed: {json.dumps(response, sort_keys=True)}")
    if not response.get("query_id") or not response.get("as_of"):
        raise RuntimeError(f"{name} omitted query_id or as_of")
    return response


def evidence_item(
    bundle: dict[str, dict[str, Any]],
    tool: str,
    claim: str,
) -> dict[str, str]:
    return {"claim": claim, "query_id": bundle[tool]["query_id"], "tool": tool}


def main() -> int:
    stage("1 - canonical events")
    canonical = read_json(FIXTURES / "canonical_events.json")
    events = canonical.get("events", [])
    if not isinstance(events, list):
        raise ValueError("canonical event fixture must contain an events array")
    print(
        f"Loaded {len(events)} representative canonical events "
        f"({canonical['total_events']} observed attempts) from fixtures."
    )
    print("This stands in for W1 native events plus W2 normalisation.")

    stage("2 - incident record")
    incident: dict[str, Any] = {
        "incident_id": INCIDENT_ID,
        "affected_cohort": COHORT,
        "change": {
            "metric": "payment_approval_conversion",
            "expected": 0.92,
            "actual": 0.64,
            "absolute_delta": -0.28,
            "relative_change": -0.3043478261,
            "unit": "ratio",
        },
        "onset": "2026-08-29T10:00:00Z",
        "persistence": {
            "is_persistent": True,
            "observed_for_seconds": 900,
            "last_observed_at": "2026-08-29T10:15:00Z",
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
        "severity": "critical",
        "lifecycle_state": "investigating",
    }
    print(json.dumps(incident, indent=2, sort_keys=True))
    print("This stands in for W2 deterministic detection, impact and prioritisation.")

    stage("3 - evidence bundle")
    requests = {
        "cohort_metrics": {"cohort": COHORT, "window": WINDOW},
        "cohort_compare": {
            "cohort": COHORT,
            "window": WINDOW,
            "compare_dimensions": ["provider", "country", "card_network"],
        },
        "drilldown": {
            "incident_id": INCIDENT_ID,
            "window": WINDOW,
            "levels": ["merchant", "provider", "country", "card_network", "issuing_bank"],
        },
        "decline_breakdown": {"cohort": COHORT, "window": WINDOW},
        "retry_stats": {"cohort": COHORT, "window": WINDOW},
        "operational_metrics": {
            "target": {
                "kind": "cohort",
                "merchant_id": "merchant-a",
                "provider": "provider-p2",
                "country": "CO",
                "card_network": "mastercard",
            },
            "window": WINDOW,
        },
        "confounding_check": {
            "dimension_a": "provider",
            "dimension_b": "issuing_bank",
            "window": WINDOW,
            "cohort": {"merchant_id": "merchant-a"},
        },
        "incident_history": {
            "merchant_id": "merchant-a",
            "cohort": {"provider": "provider-p2", "country": "CO"},
            "window": {"start": "2026-07-30T10:00:00Z", "end": WINDOW["end"]},
        },
        "external_status": {"provider": "provider-p2", "source": "provider-status-adapter"},
        "financial_impact": {"incident_id": INCIDENT_ID, "window": WINDOW},
    }
    bundle = {name: run_tool(name, request) for name, request in requests.items()}
    print(json.dumps(bundle, indent=2, sort_keys=True))
    print("Each response is a C2 subprocess result with a stable query_id and as_of timestamp.")

    stage("4 - investigation result")
    result: dict[str, Any] = {
        "incident_id": INCIDENT_ID,
        "confirmed_facts": [
            {
                "statement": "Payment-level approval conversion is 64%, down from the 92% baseline.",
                "evidence": [
                    evidence_item(
                        bundle,
                        "cohort_metrics",
                        "The affected cohort has 640 approved payments out of 1000.",
                    )
                ],
            },
            {
                "statement": "The deviation is isolated to the P2, Colombia, Mastercard slice of Merchant A.",
                "evidence": [
                    evidence_item(
                        bundle,
                        "cohort_compare",
                        "The affected target is at 64%, while the P3 sibling is at 93% and Merchant A overall is at 86%.",
                    ),
                    evidence_item(
                        bundle,
                        "drilldown",
                        "The deterministic localisation path stops at the provider/issuer boundary.",
                    ),
                ],
            },
            {
                "statement": "Retries and queue pressure amplify the payment failure symptoms.",
                "evidence": [
                    evidence_item(
                        bundle,
                        "retry_stats",
                        "1350 attempts were made for 1000 payments and queue depth rose to 318.",
                    )
                ],
            },
        ],
        "leading_hypothesis": {
            "statement": "Provider P2 degradation is the leading explanation for the affected slice.",
            "evidence": [
                evidence_item(
                    bundle,
                    "decline_breakdown",
                    "Timeouts are 71.13% of failed attempts versus 10% in the baseline.",
                ),
                evidence_item(
                    bundle,
                    "operational_metrics",
                    "P2 has 35% timeouts, p99 latency of 4200 ms and degraded service health.",
                ),
                evidence_item(
                    bundle,
                    "incident_history",
                    "The same P2 and Colombia pattern recurred twice in the prior 30 days.",
                ),
            ],
        },
        "supporting_evidence": [
            evidence_item(
                bundle,
                "cohort_compare",
                "The Provider P3 sibling remains at 93% payment-level approval conversion.",
            ),
            evidence_item(
                bundle,
                "external_status",
                "External status is unavailable, so it neither confirms nor overturns first-party evidence.",
            ),
        ],
        "competing_explanations": [
            {
                "explanation": "Bank X over-decline cannot be ruled out.",
                "evidence": [
                    evidence_item(
                        bundle,
                        "confounding_check",
                        "Provider P2 and Bank X are structurally inseparable in the observed window.",
                    )
                ],
            }
        ],
        "why_ambiguity_exists": {
            "statement": "There is no P2 traffic from another issuer and no Bank X traffic through another provider.",
            "evidence": [
                evidence_item(
                    bundle,
                    "confounding_check",
                    "The provider/issuer cross-tab has only one observed mapping per value.",
                )
            ],
        },
        "missing_evidence": [
            {
                "request": "Compare P2 traffic from another issuer or Bank X traffic through another provider.",
                "reason": "Either comparison would discriminate the two leading explanations.",
                "evidence": [
                    evidence_item(
                        bundle,
                        "confounding_check",
                        "The current cross-tab is structurally inseparable.",
                    )
                ],
            }
        ],
        "diagnostic_confidence": "medium",
        "recommended_next_action": {
            "action": "Investigate Provider P2 and collect a discriminatory provider/issuer comparison before broad rerouting.",
            "urgency": "now",
            "basis": [
                evidence_item(
                    bundle,
                    "financial_impact",
                    "The affected cohort has an estimated $112000 per hour in GMV at risk.",
                )
            ],
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    print("This stands in for W3's fixed, model-free investigation result.")

    stage("5 - surface summary and escalation")
    print("Merchant A: payment approval 92.0% -> 64.0% in the P2 / Colombia / Mastercard cohort.")
    print("Impact: $28,000 GMV at risk in 15 minutes; estimated loss rate $112,000/hour.")
    print("Diagnosis: Provider P2 is the leading hypothesis; Bank X remains confounded.")
    print("Diagnostic confidence: medium. Recommended action: investigate before broad rerouting.")
    print("Escalation channels for critical severity: dashboard + Slack-style notification + phone call.")
    print("This stands in for W4's surface and escalation layer.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"slice failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
