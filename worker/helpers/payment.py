"""Builds simulated C1 events for a merchant: a payment's full attempt chain
(clearwave.attempt.v1, topic payments.attempts) and, once the chain reaches
a terminal state, the matching payments.closed event
(clearwave.payment_closed.v1). Shapes match the schemas W2 (andres) asked
for in README-FOR-RAUL.md - see worker/registry/*.schema.json for the
frozen, authoritative field list.

An Incident scopes anomalous behaviour to a dimension combination (provider,
issuing_bank, payment_method, card_network) instead of breaking the whole
merchant - "Provider P2 declines" is a testable claim, "everything fails" is
not. Four effects, because "conversion dropped" is not the only failure
shape a real orchestrator sees:

- decline: matching attempts fail at high probability with a fixed reason.
  The default. What most people mean by "an incident".
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

Unaffected attempts always get ordinary baseline behaviour, so one worker
run carries both healthy and incident traffic at once - what the
challenge's trial-by-fire scenario actually looks like.

Magnitude and combination are tunable per Incident (decline_probability,
elevated_latency, confound_bank) rather than fixed, because the three
guaranteed scenarios in docs/scenarios.md need different shapes: a gentle
merchant-wide dip, a genuine provider/issuer confound, a sharp provider
break with elevated latency. See worker/ground_truth/scenarios.py for how
those three map onto this Incident model, and worker/ground_truth/store.py
for the quarantined C6 record (docs/contracts/hidden-truth.md) recorded
when a scenario runs.

A payment closes ("payments.closed") only once its chain stops - approved,
all attempts exhausted ("failed"), or given up on before exhausting them
("abandoned") - never after an individual attempt. See build_chain().
"""

import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from worker.helpers.merchant import Merchant
from worker.reference.banks import pick_bank
from worker.reference.geography import City, pick_city

MAX_ATTEMPTS = 3
BASELINE_DECLINE_PROBABILITY = 0.12
INCIDENT_DECLINE_PROBABILITY = 0.95
RETRY_PROBABILITY = 0.65  # chance an unaffected declined/error attempt gets retried

CARD_NETWORKS = ["visa", "mastercard", "amex", "diners", "elo", "hipercard"]
DECLINE_REASONS = [
    "insufficient_funds", "do_not_honor", "expired_card", "invalid_card",
    "incorrect_cvc", "lost_or_stolen_card", "restricted_card", "suspected_fraud",
    "issuer_unavailable", "authentication_required", "authentication_failed",
    "processing_error", "provider_timeout", "provider_error", "rate_limited",
    "currency_not_supported", "duplicate", "other",
]
DEFAULT_INCIDENT_DECLINE_REASON = "do_not_honor"
DEFAULT_INCIDENT_LATENCY_MS = 6000

DECLINE = "decline"
OUTAGE = "outage"
LATENCY = "latency"
SPIKE = "spike"
EFFECTS = (DECLINE, OUTAGE, LATENCY, SPIKE)

INCIDENT_DIMENSIONS = ("provider", "issuing_bank", "payment_method", "card_network")


@dataclass(frozen=True)
class Incident:
    """Scopes an effect to attempts matching every set dimension.

    scope example: {"provider": "stripe"} or
    {"provider": "stripe", "issuing_bank": "Nu Brasil"} - the latter only
    affects Nu Brasil cards routed through Stripe, not Stripe traffic from
    other banks.

    decline_probability tunes how aggressive effect=decline is - the manual
    CLI/inject.py default stays a near-total break (INCIDENT_DECLINE_PROBABILITY),
    but a scenario (worker/ground_truth/scenarios.py) can dial in a specific
    magnitude instead, since "an incident" is not always "everything fails".

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class PaymentAttemptBuilder:
    def __init__(self, merchant: Merchant, incident: Incident | None = None):
        self.merchant = merchant
        self.incident = incident

    def build_chain(self, forced: dict | None = None) -> list[dict]:
        """One payment's full attempt chain: 1..MAX_ATTEMPTS entries,
        stopping at the first approval, at MAX_ATTEMPTS, or when an
        unaffected declined attempt isn't retried. An incident-affected
        attempt always retries (until MAX_ATTEMPTS) - a genuinely broken
        cohort doesn't recover just because the customer tried again.

        `forced` pins dimensions instead of picking them randomly - used to
        generate spike volume concentrated on one cohort. Keys match
        INCIDENT_DIMENSIONS; anything not forced is picked as normal.
        """
        forced = forced or {}
        payment_id = f"pay_{uuid.uuid4().hex[:16]}"
        city = pick_city(self.merchant.country)
        payment_method = forced.get("payment_method") or random.choice(self.merchant.payment_methods)
        amount_minor = random.randint(1000, 50000)
        card_network = forced.get("card_network") or (
            random.choice(CARD_NETWORKS) if payment_method == "card" else None
        )
        issuing_bank = forced.get("issuing_bank") or pick_bank(self.merchant.country)
        forced_provider = forced.get("provider")

        attempts: list[dict] = []
        previous_attempt_id = None
        previous_provider = None
        for attempt_number in range(1, MAX_ATTEMPTS + 1):
            attempt = self._build_attempt(
                payment_id=payment_id,
                attempt_number=attempt_number,
                previous_attempt_id=previous_attempt_id,
                previous_provider=previous_provider,
                city=city,
                payment_method=payment_method,
                amount_minor=amount_minor,
                card_network=card_network,
                issuing_bank=issuing_bank,
                forced_provider=forced_provider,
            )
            attempts.append(attempt)

            if attempt["status"] == "approved":
                break
            affected = self.incident is not None and self.incident.matches(attempt)
            if not affected and random.random() > RETRY_PROBABILITY:
                break  # gives up early -> "abandoned", not "failed"

            previous_attempt_id = attempt["attempt_id"]
            previous_provider = attempt["provider"]
            time.sleep(0.05)  # tiny gap so attempt_ts strictly increases

        return attempts

    def build_closed(self, attempts: list[dict]) -> dict:
        """The payments.closed event for a finished chain. Call only after
        build_chain() has stopped retrying - never per-attempt.
        """
        last = attempts[-1]
        if last["status"] == "approved":
            outcome = "approved"
        elif len(attempts) >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            outcome = "abandoned"

        return {
            "schema": "clearwave.payment_closed.v1",
            "event_id": f"evt_{uuid.uuid4().hex}",
            "emitted_at": _now_iso(),
            "payment_id": last["payment_id"],
            "closed_ts": last["attempt_ts"],
            "outcome": outcome,
            "final_attempt_id": last["attempt_id"],
            "total_attempts": len(attempts),
            "merchant_id": last["merchant_id"],
            "country": last["country"],
            "payment_method": last["payment_method"],
            "amount_minor": last["amount_minor"],
            "currency": last["currency"],
        }

    def _candidate_providers(self, previous_provider: str | None) -> list[str]:
        providers = self.merchant.providers
        choices = [p for p in providers if p != previous_provider] or providers
        if self.incident is not None and self.incident.effect == OUTAGE:
            outaged = self.incident.scope.get("provider")
            rerouted = [p for p in choices if p != outaged]
            if rerouted:
                return rerouted
            # no alternative provider exists - traffic is forced through the
            # outaged one and fails downstream, handled in _build_attempt
        return choices

    def _build_attempt(
        self,
        payment_id: str,
        attempt_number: int,
        previous_attempt_id: str | None,
        previous_provider: str | None,
        city: City,
        payment_method: str,
        amount_minor: int,
        card_network: str | None,
        issuing_bank: str,
        forced_provider: str | None = None,
    ) -> dict:
        now = _now_iso()
        incident = self.incident
        if incident is not None and incident.confound_bank == issuing_bank:
            # this bank never appears through any other provider while the
            # incident is active - that is the confound, not an accident
            provider = incident.scope["provider"]
        else:
            provider = forced_provider or random.choice(self._candidate_providers(previous_provider))

        attempt = {
            "schema": "clearwave.attempt.v1",
            "event_id": f"evt_{uuid.uuid4().hex}",
            "emitted_at": now,

            "payment_id": payment_id,
            "attempt_id": f"att_{uuid.uuid4().hex[:16]}",
            "attempt_number": attempt_number,
            "is_retry": attempt_number > 1,
            "previous_attempt_id": previous_attempt_id,

            "attempt_ts": now,

            "merchant_id": self.merchant.merchant_id,
            "provider": provider,
            "provider_connection": None,
            "payment_method": payment_method,
            "card_network": card_network,
            "country": self.merchant.country,
            "issuing_bank": issuing_bank,
            "bin": None,

            "status": "approved",
            "decline_reason": None,
            "provider_raw_code": None,

            "amount_minor": amount_minor,
            "currency": self.merchant.currency,

            "latency_ms": random.randint(80, 400),
            "timed_out": False,
            "http_status": 200,
            "queue_delay_ms": None,
            "service_id": f"w1-worker-{self.merchant.merchant_type}",
            "deployment_id": "worker-local",

            "city": city.name,
            "lat": city.lat,
            "lon": city.lon,
        }

        affected = incident is not None and incident.matches(attempt)
        if affected and incident.effect == DECLINE:
            if random.random() < incident.decline_probability:
                self._apply_incident_decline(attempt)
            elif random.random() < BASELINE_DECLINE_PROBABILITY:
                self._apply_baseline_decline(attempt)
            if incident.elevated_latency:
                self._apply_latency_degradation(attempt)
        elif affected and incident.effect == OUTAGE:
            # only reachable when no alternative provider exists - the
            # outaged provider is otherwise excluded before this point
            self._apply_forced_outage_failure(attempt)
        elif affected and incident.effect == LATENCY:
            self._apply_latency_degradation(attempt)
            if random.random() < BASELINE_DECLINE_PROBABILITY:
                self._apply_baseline_decline(attempt)
        elif random.random() < BASELINE_DECLINE_PROBABILITY:
            self._apply_baseline_decline(attempt)

        return attempt

    def _apply_incident_decline(self, attempt: dict) -> None:
        attempt["status"] = "declined"
        attempt["decline_reason"] = self.incident.decline_reason
        attempt["provider_raw_code"] = "05"

    def _apply_forced_outage_failure(self, attempt: dict) -> None:
        attempt["status"] = "error"
        attempt["decline_reason"] = "provider_timeout"
        attempt["timed_out"] = True
        attempt["http_status"] = random.choice([502, 503, 504])
        attempt["latency_ms"] = random.randint(5000, 12000)

    def _apply_latency_degradation(self, attempt: dict) -> None:
        attempt["latency_ms"] = self.incident.latency_ms
        attempt["queue_delay_ms"] = int(self.incident.latency_ms * 0.6)

    def _apply_baseline_decline(self, attempt: dict) -> None:
        is_error = random.random() < 0.2
        if is_error:
            attempt["status"] = "error"
            attempt["decline_reason"] = random.choice(
                ["provider_timeout", "provider_error", "processing_error", "rate_limited"]
            )
            attempt["timed_out"] = attempt["decline_reason"] == "provider_timeout"
            attempt["http_status"] = random.choice([500, 502, 503, 504])
            attempt["latency_ms"] = random.randint(2000, 8000)
        else:
            attempt["status"] = "declined"
            attempt["decline_reason"] = random.choice(DECLINE_REASONS)
            attempt["provider_raw_code"] = str(random.randint(1, 99)).zfill(2)
