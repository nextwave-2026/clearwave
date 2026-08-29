"""Incident: scopes an effect to attempts matching every set dimension
(provider, issuing_bank, payment_method, card_network) instead of breaking
the whole merchant - "Provider P2 declines" is a testable claim,
"everything fails" is not.

Four effects, because "conversion dropped" is not the only failure shape a
real orchestrator sees:

- decline: matching attempts fail at a tunable probability with a fixed
  reason. The default. What most people mean by "an incident".
- outage: the scoped provider is excluded from routing entirely - volume
  for it drops to zero instead of showing up declined. A different signal
  than decline: nothing to see in the decline mix, only in cohort volume.
  Only valid scoped to provider - a bank or method isn't something the
  merchant can route around.
- latency: matching attempts still resolve normally (approve/decline at
  baseline rates) but latency_ms/queue_delay_ms spike - tests whether
  operational evidence gets used even when conversion doesn't move.
- spike: no per-attempt effect; worker.py generates extra chains forced
  into this scope on top of normal traffic, simulating a real volume
  surge rather than the retry amplification a decline already produces.

Magnitude and combination are tunable (decline_probability, elevated_latency,
confound_bank) rather than fixed, because the three guaranteed scenarios in
docs/scenarios.md need different shapes: a gentle merchant-wide dip, a
genuine provider/issuer confound, a sharp provider break with elevated
latency. See worker/ground_truth/scenarios.py for how those three map onto
this model.
"""

from dataclasses import dataclass, field

DECLINE = "decline"
OUTAGE = "outage"
LATENCY = "latency"
SPIKE = "spike"
EFFECTS = (DECLINE, OUTAGE, LATENCY, SPIKE)

INCIDENT_DIMENSIONS = ("provider", "issuing_bank", "payment_method", "card_network")

DEFAULT_INCIDENT_DECLINE_REASON = "do_not_honor"
DEFAULT_INCIDENT_LATENCY_MS = 6000
INCIDENT_DECLINE_PROBABILITY = 0.95  # manual CLI/inject.py default: near-total break


@dataclass(frozen=True)
class Incident:
    """scope example: {"provider": "stripe"} or
    {"provider": "stripe", "issuing_bank": "Nu Brasil"} - the latter only
    affects Nu Brasil cards routed through Stripe, not Stripe traffic from
    other banks.

    decline_probability tunes how aggressive effect=decline is - the manual
    CLI/inject.py default stays a near-total break (INCIDENT_DECLINE_PROBABILITY),
    but a scenario can dial in a specific magnitude instead, since "an
    incident" is not always "everything fails".

    elevated_latency layers a latency/queue spike on top of effect=decline or
    effect=outage, for scenarios where both signals move together (a
    degrading provider looks slow *and* increasingly declines, not one or
    the other).

    confound_bank, combined with a scope naming both provider and
    issuing_bank, forces every attempt from that bank - not just the ones
    that would have matched anyway - through the scoped provider. That is
    what makes the bank and provider genuinely observationally inseparable
    for the provider-issuer-confounded scenario, instead of merely
    correlated by chance.
    """

    scope: dict = field(default_factory=dict)
    effect: str = DECLINE
    decline_reason: str = DEFAULT_INCIDENT_DECLINE_REASON
    decline_probability: float = INCIDENT_DECLINE_PROBABILITY
    latency_ms: int = DEFAULT_INCIDENT_LATENCY_MS
    elevated_latency: bool = False
    confound_bank: str | None = None

    def __post_init__(self):
        unknown = set(self.scope) - set(INCIDENT_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown incident dimension(s): {sorted(unknown)}, expected one of: {INCIDENT_DIMENSIONS}")
        if not self.scope:
            raise ValueError("an Incident needs at least one scoped dimension")
        if self.effect not in EFFECTS:
            raise ValueError(f"unknown effect {self.effect!r}, expected one of: {EFFECTS}")
        if self.effect == OUTAGE and set(self.scope) != {"provider"}:
            raise ValueError("an outage incident must be scoped to provider only - a bank or method outage isn't something the merchant can route around")
        if self.confound_bank is not None and not {"provider", "issuing_bank"} <= set(self.scope):
            raise ValueError("confound_bank requires both provider and issuing_bank in scope")

    def matches(self, attempt: dict) -> bool:
        return all(attempt.get(dimension) == value for dimension, value in self.scope.items())
