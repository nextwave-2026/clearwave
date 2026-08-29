"""Builds simulated C1 events for a merchant: a payment's full attempt chain
(clearwave.attempt.v1, topic payments.attempts) and, once the chain reaches
a terminal state, the matching payments.closed event
(clearwave.payment_closed.v1). Shapes match the schemas W2 (andres) asked
for in README-FOR-RAUL.md - see worker/registry/*.schema.json for the
frozen, authoritative field list.

Incident scoping/effects live in worker/helpers/incident.py, not here -
this module only applies whatever Incident it's given. Unaffected attempts
always get ordinary baseline behaviour, so one worker run carries both
healthy and incident traffic at once - what the challenge's trial-by-fire
scenario actually looks like.

A payment closes ("payments.closed") only once its chain stops - approved,
all attempts exhausted ("failed"), or given up on before exhausting them
("abandoned") - never after an individual attempt. See build_chain().
"""

import random
import time
import uuid
from datetime import datetime, timezone

from worker.helpers.incident import DECLINE, LATENCY, OUTAGE
from worker.helpers.merchant import Merchant
from worker.reference.banks import pick_bank
from worker.reference.geography import City, pick_city

MAX_ATTEMPTS = 3
BASELINE_DECLINE_PROBABILITY = 0.12
RETRY_PROBABILITY = 0.65  # chance an unaffected declined/error attempt gets retried

CARD_NETWORKS = ["visa", "mastercard", "amex", "diners", "elo", "hipercard"]
DECLINE_REASONS = [
    "insufficient_funds", "do_not_honor", "expired_card", "invalid_card",
    "incorrect_cvc", "lost_or_stolen_card", "restricted_card", "suspected_fraud",
    "issuer_unavailable", "authentication_required", "authentication_failed",
    "processing_error", "provider_timeout", "provider_error", "rate_limited",
    "currency_not_supported", "duplicate", "other",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class PaymentAttemptBuilder:
    def __init__(self, merchant: Merchant, incident=None):
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
