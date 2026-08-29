"""Deterministic canonical events for testing the detection plane.

This is TEST DATA, not a simulator. W1 owns the simulated world; this module
exists only so the detector's behaviour can be asserted without waiting for it,
and it is seeded so every run produces identical input.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

BASE = datetime(2026, 8, 30, 4, 0, 0, tzinfo=timezone.utc)

MERCHANTS = ("merchant-a", "merchant-b")
PROVIDERS = ("provider-p2", "provider-p3")
COUNTRIES = ("CO", "MX")
BANKS = ("bank-x", "bank-y")


def _event(index: int, minute: int, **overrides: Any) -> dict[str, Any]:
    payment = f"pay-{index:05d}"
    event = {
        "payment_id": payment,
        "attempt_id": f"att-{index:05d}-1",
        "attempt_number": 1,
        "occurred_at": (BASE + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Each dimension varies on a different stride so that no two are
        # accidentally confounded. A generator that pairs provider with country
        # one-to-one would make every confounding test pass for the wrong
        # reason.
        "merchant_id": MERCHANTS[(index // 8) % len(MERCHANTS)],
        "provider": PROVIDERS[index % len(PROVIDERS)],
        "payment_method": "card",
        "card_network": "mastercard",
        "country": COUNTRIES[(index // 2) % len(COUNTRIES)],
        "issuing_bank": BANKS[(index // 4) % len(BANKS)],
        "status": "approved",
        "amount": 100.0,
        "currency": "USD",
        "latency_ms": 400,
    }
    event.update(overrides)
    return event


def healthy(minutes: int = 80, per_minute: int = 20, seed: int = 7) -> list[dict[str, Any]]:
    """Normal traffic at roughly 92% conversion, with ordinary noise."""
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            approved = rng.random() < 0.92
            events.append(
                _event(
                    index,
                    minute,
                    status="approved" if approved else "declined",
                    **({} if approved else {"normalized_decline_reason": "insufficient_funds"}),
                )
            )
    return events


def with_provider_incident(
    minutes: int = 80,
    per_minute: int = 20,
    onset_minute: int = 65,
    seed: int = 7,
) -> list[dict[str, Any]]:
    """Healthy traffic, then provider-p2 in CO collapses from the onset minute.

    Retries are emitted for the failing cohort so attempt-level and
    payment-level conversion diverge the way a real degradation would.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            base = _event(index, minute)
            degraded = (
                minute >= onset_minute
                and base["provider"] == "provider-p2"
                and base["country"] == "CO"
            )
            approved = rng.random() < (0.25 if degraded else 0.92)
            base["status"] = "approved" if approved else "declined"
            if not approved:
                base["normalized_decline_reason"] = "do_not_honor" if degraded else "insufficient_funds"
            events.append(base)
            if degraded and not approved:
                retry = dict(base)
                retry["attempt_id"] = f"att-{index:05d}-2"
                retry["attempt_number"] = 2
                retry["status"] = "timeout"
                retry["normalized_decline_reason"] = "timeout"
                retry["latency_ms"] = 8000
                events.append(retry)
    return events


def confounded(minutes: int = 80, per_minute: int = 20, seed: int = 7) -> list[dict[str, Any]]:
    """Every provider-p2 payment comes from bank-x and vice versa.

    The evidence genuinely cannot separate a provider cause from an issuer
    cause here, and the correct behaviour is to say so.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            provider, bank = (("provider-p2", "bank-x") if index % 2 else ("provider-p3", "bank-y"))
            events.append(
                _event(
                    index,
                    minute,
                    provider=provider,
                    issuing_bank=bank,
                    status="approved" if rng.random() < 0.9 else "declined",
                    **({} if rng.random() < 0.9 else {}),
                )
            )
            if events[-1]["status"] == "declined":
                events[-1]["normalized_decline_reason"] = "do_not_honor"
    return events


def _set_approval_count(
    events: list[dict[str, Any]], indexes: list[int], approved_count: int, rng: random.Random
) -> None:
    """Set deterministic outcomes for one generated replay segment."""
    if not 0 <= approved_count <= len(indexes):
        raise ValueError("approved_count must fit the replay segment")
    approved = set(rng.sample(indexes, approved_count))
    for index in indexes:
        event = events[index]
        if index in approved:
            event["status"] = "approved"
            event.pop("normalized_decline_reason", None)
        else:
            event["status"] = "declined"
            event["normalized_decline_reason"] = "do_not_honor"


def high_impact_small_percentage(seed: int = 20260830) -> list[dict[str, Any]]:
    """Generate the high-volume replay whose platform drop hides Merchant A."""
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    segments: dict[tuple[str, int], list[int]] = {}
    index = 0
    for merchant, per_minute in (("merchant-a", 500), ("merchant-b", 100)):
        for minute in range(80):
            segment = segments.setdefault((merchant, minute), [])
            for _ in range(per_minute):
                index += 1
                segment.append(len(events))
                events.append(
                    _event(
                        index,
                        minute,
                        merchant_id=merchant,
                        provider=("provider-p2", "provider-p3")[index % 2],
                        country=("CO", "MX")[(index // 2) % 2],
                        issuing_bank=("bank-x", "bank-y")[(index // 4) % 2],
                        occurred_at=(BASE + timedelta(minutes=minute, seconds=index % 60)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        status="approved",
                    )
                )

    for merchant, baseline_approved, final_approved in (
        ("merchant-a", 27603, 2237),
        ("merchant-b", 5518, 474),
    ):
        baseline = [
            index
            for minute in range(15, 75)
            for index in segments[(merchant, minute)]
        ]
        final = [
            index
            for minute in range(75, 80)
            for index in segments[(merchant, minute)]
        ]
        _set_approval_count(events, baseline, baseline_approved, rng)
        _set_approval_count(events, final, final_approved, rng)
    return events


def confounded_incident(seed: int = 20260830) -> list[dict[str, Any]]:
    """Generate a one-to-one P2/Bank-X replay with a degraded P2 window."""
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    segments: dict[tuple[str, int], list[int]] = {}
    index = 0
    for minute in range(80):
        for _ in range(100):
            index += 1
            provider, bank = (("provider-p2", "bank-x") if index % 2 else ("provider-p3", "bank-y"))
            segment = segments.setdefault((provider, minute), [])
            segment.append(len(events))
            events.append(
                _event(
                    index,
                    minute,
                    merchant_id="merchant-a",
                    provider=provider,
                    issuing_bank=bank,
                    country="CO",
                    occurred_at=(BASE + timedelta(minutes=minute, seconds=index % 60)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    status="approved",
                )
            )

    for provider, baseline_approved, final_approved in (
        ("provider-p2", 2760, 152),
        ("provider-p3", 2760, 229),
    ):
        baseline = [
            index
            for minute in range(15, 75)
            for index in segments[(provider, minute)]
        ]
        final = [
            index
            for minute in range(75, 80)
            for index in segments[(provider, minute)]
        ]
        _set_approval_count(events, baseline, baseline_approved, rng)
        _set_approval_count(events, final, final_approved, rng)
    return events
