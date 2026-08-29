"""Builds simulated C1 events for a merchant: a payment's full attempt chain
(clearwave.attempt.v1, topic payments.attempts) and, once the chain reaches
a terminal state, the matching payments.closed event
(clearwave.payment_closed.v1). Shapes match the schemas W2 (andres) asked
for in README-FOR-RAUL.md - see worker/registry/*.schema.json for the
frozen, authoritative field list.

Two generation modes, picked by a flag on the builder:

- "normal": healthy traffic - most payments approve on the first attempt, a
  minority decline and either retry (mostly succeeding) or get abandoned.
  Matches the non-stationary baseline W1 owns (PRD section 9).
- "anomaly": every attempt in the chain is forced to decline with the same
  reason, so a chain either exhausts all attempts (outcome "failed") rather
  than recovering on retry - this is what makes retry amplification show up
  as evidence (PRD section 8) instead of looking like normal noise.

This is the seed for incident injection (PRD section 5, 9, 26, 27), not the
real thing yet: it only varies enough to produce something detectable, one
merchant-wide switch rather than a scoped cohort. Which dimensions misbehave,
for how long, and the hidden ground truth needed to grade a diagnosis are a
later W1 increment - deliberately not decided here.

A payment closes ("payments.closed") only once its chain stops - approved,
all attempts exhausted ("failed"), or given up on before exhausting them
("abandoned") - never after an individual attempt. See build_chain().
"""

import random
import time
import uuid
from datetime import datetime, timezone

from faker import Faker

from worker.helpers.merchant import Merchant
from worker.reference.geography import City, pick_city

NORMAL = "normal"
ANOMALY = "anomaly"
MODES = (NORMAL, ANOMALY)

MAX_ATTEMPTS = 3
NORMAL_DECLINE_PROBABILITY = 0.12
ANOMALY_DECLINE_PROBABILITY = 0.95
RETRY_PROBABILITY = 0.65  # chance a declined/error attempt (normal mode) gets retried

CARD_NETWORKS = ["visa", "mastercard", "amex", "diners", "elo", "hipercard"]
DECLINE_REASONS = [
    "insufficient_funds", "do_not_honor", "expired_card", "invalid_card",
    "incorrect_cvc", "lost_or_stolen_card", "restricted_card", "suspected_fraud",
    "issuer_unavailable", "authentication_required", "authentication_failed",
    "processing_error", "provider_timeout", "provider_error", "rate_limited",
    "currency_not_supported", "duplicate", "other",
]
ANOMALY_DECLINE_REASON = "do_not_honor"  # fixed, not random - a real incident
# looks like the same reason recurring, not noise. See _apply_decline.

fake = Faker()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class PaymentAttemptBuilder:
    def __init__(self, merchant: Merchant, mode: str = NORMAL):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}, expected one of: {MODES}")
        self.merchant = merchant
        self.mode = mode

    def build_chain(self) -> list[dict]:
        """One payment's full attempt chain: 1..MAX_ATTEMPTS entries,
        stopping at the first approval, at MAX_ATTEMPTS, or when a declined
        attempt isn't retried (normal mode only - anomaly mode always
        retries, since the whole cohort is broken, not one customer).
        """
        payment_id = f"pay_{uuid.uuid4().hex[:16]}"
        city = pick_city(self.merchant.country)
        payment_method = random.choice(self.merchant.payment_methods)
        amount_minor = random.randint(1000, 50000)

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
            )
            attempts.append(attempt)

            if attempt["status"] == "approved":
                break
            if self.mode == NORMAL and random.random() > RETRY_PROBABILITY:
                break  # gives up early -> "abandoned", not "failed"

            previous_attempt_id = attempt["attempt_id"]
            previous_provider = attempt["provider"]
            # tiny gap so attempt_ts strictly increases within the chain
            time.sleep(0.05)

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

    def _build_attempt(
        self,
        payment_id: str,
        attempt_number: int,
        previous_attempt_id: str | None,
        previous_provider: str | None,
        city: City,
        payment_method: str,
        amount_minor: int,
    ) -> dict:
        now = _now_iso()
        providers = self.merchant.providers
        # reroute away from the failing provider on retry, when there's a choice
        choices = [p for p in providers if p != previous_provider] or providers
        provider = random.choice(choices)

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
            "card_network": random.choice(CARD_NETWORKS) if payment_method == "card" else None,
            "country": self.merchant.country,
            "issuing_bank": f"{fake.company()} Bank",
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

        decline_probability = (
            ANOMALY_DECLINE_PROBABILITY if self.mode == ANOMALY else NORMAL_DECLINE_PROBABILITY
        )
        if random.random() < decline_probability:
            self._apply_decline(attempt)

        return attempt

    def _apply_decline(self, attempt: dict) -> None:
        if self.mode == ANOMALY:
            attempt["status"] = "declined"
            attempt["decline_reason"] = ANOMALY_DECLINE_REASON
            attempt["provider_raw_code"] = "05"
            return

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
