"""C1b canonical ingestion event: the one normalised model everything reads.

W1 emits native per-merchant shapes and registers them. W2 normalises those
into this model. Nothing downstream of here ever sees a native shape.

Two invariants carry the weight:

* payment identity and attempt identity are both preserved, so payment-level
  and attempt-level conversion can never be accidentally collapsed;
* ``normalized_decline_reason`` comes from a closed vocabulary, so the decline
  distribution is comparable across providers. The provider's own code is
  carried through unparsed for evidence, never for arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import config

# The dimensions a cohort may be sliced on. This set is the whole universe:
# adding one is a canonical-schema change, never a local addition.
DIMENSIONS = (
    "merchant_id",
    "provider",
    "payment_method",
    "card_network",
    "country",
    "issuing_bank",
)

STATUSES = ("approved", "declined", "error", "timeout", "pending")
FAILED_STATUSES = ("declined", "error", "timeout")

# Closed vocabulary. W2 maps each provider's native codes into these.
DECLINE_REASONS = (
    "insufficient_funds",
    "do_not_honor",
    "expired_card",
    "invalid_card",
    "incorrect_cvc",
    "lost_or_stolen_card",
    "restricted_card",
    "suspected_fraud",
    "issuer_decline",
    "issuer_unavailable",
    "authentication_required",
    "authentication_failed",
    "processing_error",
    "provider_error",
    "timeout",
    "rate_limited",
    "currency_not_supported",
    "duplicate",
    "other",
)

REQUIRED = (
    "payment_id",
    "attempt_id",
    "attempt_number",
    "occurred_at",
    "merchant_id",
    "provider",
    "payment_method",
    "country",
    "status",
    "amount",
    "currency",
)


class InvalidEvent(ValueError):
    """A canonical event that cannot be counted. Rejected, never guessed at."""


def parse_timestamp(value: str) -> datetime:
    """Parse an RFC 3339 UTC timestamp into an aware datetime."""
    if not isinstance(value, str) or not value:
        raise InvalidEvent("occurred_at must be a non-empty RFC 3339 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidEvent(f"occurred_at is not a valid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_usd(amount: float, currency: str) -> float:
    """Convert to the reporting currency using the frozen table.

    An unknown currency is an error rather than a silent pass-through: a wrong
    money figure is worse than a missing one, because everything downstream
    cites it.
    """
    rate = config.FX_TO_USD.get(currency)
    if rate is None:
        raise InvalidEvent(f"no frozen FX rate for currency {currency!r}")
    return float(amount) * rate


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one canonical event and return it in storage form.

    Raises InvalidEvent rather than repairing anything. A record we cannot
    trust is dead-lettered, because a quietly wrong count is undetectable
    later.
    """
    if not isinstance(raw, dict):
        raise InvalidEvent("event must be a JSON object")

    missing = [field for field in REQUIRED if raw.get(field) in (None, "")]
    if missing:
        raise InvalidEvent(f"missing required field(s): {', '.join(missing)}")

    status = raw["status"]
    if status not in STATUSES:
        raise InvalidEvent(f"status {status!r} is not one of {STATUSES}")

    reason = raw.get("normalized_decline_reason")
    if status in FAILED_STATUSES:
        if not reason:
            raise InvalidEvent(f"status {status!r} requires normalized_decline_reason")
        if reason not in DECLINE_REASONS:
            raise InvalidEvent(f"decline reason {reason!r} is outside the closed vocabulary")
    elif reason:
        raise InvalidEvent(f"status {status!r} must not carry a decline reason")

    if raw["payment_method"] == "card" and not raw.get("card_network"):
        raise InvalidEvent("card payments require card_network")

    try:
        attempt_number = int(raw["attempt_number"])
    except (TypeError, ValueError) as exc:
        raise InvalidEvent("attempt_number must be an integer") from exc
    if attempt_number < 1:
        raise InvalidEvent("attempt_number is 1-based")

    occurred_at = parse_timestamp(raw["occurred_at"])

    return {
        "event_id": raw.get("event_id") or raw["attempt_id"],
        "payment_id": str(raw["payment_id"]),
        "attempt_id": str(raw["attempt_id"]),
        "attempt_number": attempt_number,
        "occurred_at": occurred_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "occurred_epoch": int(occurred_at.timestamp()),
        "merchant_id": str(raw["merchant_id"]),
        "provider": str(raw["provider"]),
        "payment_method": str(raw["payment_method"]),
        "card_network": raw.get("card_network") or None,
        "country": str(raw["country"]),
        "issuing_bank": raw.get("issuing_bank") or None,
        "status": status,
        "normalized_decline_reason": reason or None,
        "provider_raw_code": raw.get("provider_raw_code") or None,
        "amount_usd": to_usd(raw["amount"], raw["currency"]),
        "currency": str(raw["currency"]),
        "latency_ms": raw.get("latency_ms"),
        "queue_depth": raw.get("queue_depth"),
        "queue_delay_ms": raw.get("queue_delay_ms"),
        "deployment_id": raw.get("deployment_id") or None,
        "service_id": raw.get("service_id") or None,
    }


def bucket_of(occurred_epoch: int) -> int:
    """Floor an event time to its bucket. Event time only, never wall clock."""
    return occurred_epoch - (occurred_epoch % config.BUCKET_SECONDS)
