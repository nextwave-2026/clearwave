"""The three guaranteed demo scenarios (docs/scenarios.md), mapped onto our
actual merchants/providers/banks. The catalogue's placeholder names
("provider-p2", "bank-x") describe structure and representative magnitude,
not literal values - filling in real ones (adyen, Nu Brasil) is exactly the
call docs/scenarios.md leaves to the simulator.

Each scenario is pinned to the one merchant whose profile can express it:
provider-issuer-confounded needs a provider and a bank that coexist on the
same merchant (merchant-c, BR - has both adyen and Nu Brasil).
provider-degradation wants a provider shared across merchants for a
provider-wide (not single-merchant) story; adyen also serves merchant-b.
high-impact-small-percentage wants a broad, high-volume dip - merchant-a's
archetype (PRD section 20) is the high-volume one.

Only these three are wired. The other nine scenarios in docs/scenarios.md
are catalogued and evaluator-eligible but explicitly not build-guaranteed
(derek's DECISIONS.md 19:17Z demo-scope entry) - do not add them here
without checking that decision still holds.
"""

from dataclasses import dataclass
from typing import Any, Callable

from worker.helpers.incident import DECLINE, Incident

ScenarioId = str


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    scenario_name: str
    merchant_id: str
    failure_mode: str
    strength_metric: str
    strength_baseline: float
    strength_target: float
    strength_unit: str
    strength_direction: str
    build_incident: Callable[[], Incident]

    @property
    def strength(self) -> dict[str, Any]:
        return {
            "metric": self.strength_metric,
            "baseline": self.strength_baseline,
            "target": self.strength_target,
            "unit": self.strength_unit,
            "direction": self.strength_direction,
        }


def _provider_degradation() -> Incident:
    return Incident(
        scope={"provider": "adyen"},
        effect=DECLINE,
        decline_probability=0.55,
        decline_reason="provider_timeout",
        elevated_latency=True,
        latency_ms=7000,
    )


def _provider_issuer_confounded() -> Incident:
    return Incident(
        scope={"provider": "adyen", "issuing_bank": "Nu Brasil"},
        effect=DECLINE,
        decline_probability=0.85,
        decline_reason="issuer_unavailable",
        confound_bank="Nu Brasil",
    )


def _high_impact_small_percentage() -> Incident:
    return Incident(
        scope={"payment_method": "card"},
        effect=DECLINE,
        decline_probability=0.22,
        decline_reason="do_not_honor",
    )


SCENARIOS: dict[ScenarioId, ScenarioDefinition] = {
    "provider-degradation": ScenarioDefinition(
        scenario_id="provider-degradation",
        scenario_name="Provider degradation across cohorts",
        merchant_id="merchant-c",
        failure_mode="provider_timeout_and_latency_degradation",
        strength_metric="timeout_rate",
        strength_baseline=0.05,
        strength_target=0.35,
        strength_unit="ratio",
        strength_direction="increase",
        build_incident=_provider_degradation,
    ),
    "provider-issuer-confounded": ScenarioDefinition(
        scenario_id="provider-issuer-confounded",
        scenario_name="Provider versus issuer observational confounder",
        merchant_id="merchant-c",
        failure_mode="issuer_over_decline_confounded_with_provider",
        strength_metric="payment_approval_conversion",
        strength_baseline=0.92,
        strength_target=0.64,
        strength_unit="ratio",
        strength_direction="decrease",
        build_incident=_provider_issuer_confounded,
    ),
    "high-impact-small-percentage": ScenarioDefinition(
        scenario_id="high-impact-small-percentage",
        scenario_name="High-impact small conversion change",
        merchant_id="merchant-a",
        failure_mode="broad_small_decline_regression",
        strength_metric="payment_approval_conversion",
        strength_baseline=0.92,
        strength_target=0.895,
        strength_unit="ratio",
        strength_direction="decrease",
        build_incident=_high_impact_small_percentage,
    ),
}
