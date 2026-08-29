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

# ISO 4217 exponents for the currencies in the frozen FX table. Minor units are
# only meaningful against the right exponent, and assuming 2 everywhere is
# wrong for JPY, KRW and friends.
MINOR_UNIT_EXPONENT = {
    "USD": 2,
    "COP": 2,
    "BRL": 2,
    "MXN": 2,
    "EUR": 2,
    "JPY": 0,
    "CLP": 0,
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
        mapped["normalized_decline_reason"] = reason
    mapped.pop("decline_reason", None)
    mapped.pop("attempt_ts", None)
    return mapped


def attempt_v1(record: dict[str, Any]) -> dict[str, Any]:
    """Map the pre-normalisation envelope circulated to W1.

    Differences from C1b, all mechanical: event time is ``attempt_ts``, money
    is an integer in minor units, and the decline field is ``decline_reason``.
    """
    mapped = canonical(record)
    minor = record.get("amount_minor")
    currency = record.get("currency")
    if minor is not None and currency:
        exponent = MINOR_UNIT_EXPONENT.get(currency)
        if exponent is None:
            raise UnknownShape(f"no minor-unit exponent registered for currency {currency!r}")
        mapped["amount"] = int(minor) / (10 ** exponent)
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
