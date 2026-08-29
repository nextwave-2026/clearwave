"""Native source shapes in, one canonical event out.

W1 emits a native shape per merchant and registers it; normalisation is W2's.
A mapper is a pure function from one native record to a C1b dict, and the
registry is how a new source is added without touching anything downstream.

Two mappers ship today because two shapes already exist in the wild:

* ``canonical`` - already C1b, or near enough. This is the vertical slice's
  fixture shape.
* ``attempt_v1`` - the envelope circulated to W1 before normalisation moved to
  W2. It uses ``attempt_ts``, integer minor units, and ``decline_reason``.

Accepting both costs a few lines and removes a coordination round trip: whoever
built to either shape was right, and neither has to be redone.
"""

from __future__ import annotations

from typing import Any, Callable

from . import schema

# The ISO 4217 exponent table lives in `detector/schema.py`, beside the FX
# conversion it feeds. Re-exported here because this is where callers look for
# it.
MINOR_UNIT_EXPONENT = schema.MINOR_UNIT_EXPONENT


# W1's frozen native decline vocabulary, mapped onto C1b's closed one. Every
# value of the enum in `worker/registry/payment_attempt.schema.json` appears
# here exactly once, and `tests/test_mappers.py` fails if the two vocabularies
# ever drift apart again - which is the only way this class of bug is visible,
# because the symptom is a quiet dead-letter rather than a crash.
#
# Most values are identical in both vocabularies and map to themselves. The one
# real translation is `provider_timeout`, which W1 emits under exactly the
# provider degradation the demo turns on: C1b spells that `timeout`, so the
# mapper renames it and carries the native spelling through in
# `provider_raw_code`. Widening C1b to swallow the native code instead would
# leave two names for one thing in the decline distribution, and decline mix is
# the discriminator the whole diagnosis leans on.
NATIVE_DECLINE_REASONS = {
    "insufficient_funds": "insufficient_funds",
    "do_not_honor": "do_not_honor",
    "expired_card": "expired_card",
    "invalid_card": "invalid_card",
    "incorrect_cvc": "incorrect_cvc",
    "lost_or_stolen_card": "lost_or_stolen_card",
    "restricted_card": "restricted_card",
    "suspected_fraud": "suspected_fraud",
    "issuer_unavailable": "issuer_unavailable",
    "authentication_required": "authentication_required",
    "authentication_failed": "authentication_failed",
    "processing_error": "processing_error",
    "provider_timeout": "timeout",
    "provider_error": "provider_error",
    "rate_limited": "rate_limited",
    "currency_not_supported": "currency_not_supported",
    "duplicate": "duplicate",
    "other": "other",
}


class UnknownShape(ValueError):
    """No registered mapper recognises this record."""


def _first(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def canonical(record: dict[str, Any]) -> dict[str, Any]:
    """Pass through a record that already speaks C1b, tolerating aliases."""
    mapped = dict(record)
    occurred = _first(record, "occurred_at", "attempt_ts", "timestamp", "event_time")
    if occurred is not None:
        mapped["occurred_at"] = occurred
    reason = _first(record, "normalized_decline_reason", "decline_reason")
    if reason is not None:
        mapped["normalized_decline_reason"] = normalise_decline_reason(reason)
        if mapped["normalized_decline_reason"] != reason and not mapped.get("provider_raw_code"):
            # The native spelling is evidence. It is preserved unparsed rather
            # than lost to the rename, exactly as C1b requires.
            mapped["provider_raw_code"] = reason
    mapped.pop("decline_reason", None)
    mapped.pop("attempt_ts", None)
    return mapped


def normalise_decline_reason(reason: Any) -> Any:
    """Translate one native decline code into the C1b closed vocabulary.

    An unmapped value is returned unchanged so that `detector/schema.py` refuses
    it by name and dead-letters the record. Guessing a canonical target here
    would turn an unknown code into a plausible-looking count, which is the one
    outcome worse than a visible rejection.
    """
    if not isinstance(reason, str):
        return reason
    return NATIVE_DECLINE_REASONS.get(reason, reason)


def attempt_v1(record: dict[str, Any]) -> dict[str, Any]:
    """Map the pre-normalisation envelope circulated to W1.

    Differences from C1b, all mechanical: event time is ``attempt_ts``, money
    is an integer in minor units, and the decline field is ``decline_reason``.
    """
    mapped = canonical(record)
    minor = record.get("amount_minor")
    currency = record.get("currency")
    if minor is not None and currency:
        try:
            mapped["amount"] = schema.minor_to_major(minor, currency)
        except schema.UnknownMinorUnit as exc:
            raise UnknownShape(str(exc)) from exc
        mapped.pop("amount_minor", None)
    if record.get("timed_out") and mapped.get("status") in (None, "error"):
        mapped["status"] = "timeout"
    return mapped


REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "canonical": canonical,
    "clearwave.attempt.v1": attempt_v1,
}


def detect_shape(record: dict[str, Any]) -> str:
    """Name the mapper for one record.

    An explicit ``schema`` field wins, because a source that declares itself
    should never be guessed at. Otherwise we infer from the two fields that
    actually differ between the known shapes.
    """
    declared = record.get("schema")
    if isinstance(declared, str) and declared in REGISTRY:
        return declared
    if "amount_minor" in record or "attempt_ts" in record:
        return "clearwave.attempt.v1"
    return "canonical"


def to_canonical(record: dict[str, Any], shape: str | None = None) -> dict[str, Any]:
    """Normalise one native record. Raises UnknownShape on an unregistered one."""
    if not isinstance(record, dict):
        raise UnknownShape("event must be a JSON object")
    name = shape or detect_shape(record)
    mapper = REGISTRY.get(name)
    if mapper is None:
        raise UnknownShape(f"no mapper registered for shape {name!r}")
    mapped = mapper(record)
    mapped.pop("schema", None)
    return mapped
