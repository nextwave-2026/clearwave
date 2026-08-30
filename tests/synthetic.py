"""Deterministic canonical events for testing the detection plane.

This is TEST DATA, not a simulator. W1 owns the simulated world; this module
exists only so the detector's behaviour can be asserted without waiting for it,
and it is seeded so every run produces identical input.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
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


def merchant_scale(
    minutes: int = 400,
    onset_minute: int = 375,
    small_per_minute: int = 6,
    small_amount: float = 7.50,
    large_per_minute: int = 20,
    large_amount: float = 90.0,
    seed: int = 11,
) -> list[dict[str, Any]]:
    """Two merchants on wildly different scales, one of them collapsing.

    ``merchant-small`` is a low-volume, low-ticket business: its whole normal
    hour is worth a few thousand dollars, so losing most of its traffic still
    costs well under $2,000 an hour and the absolute-dollar ladder can never
    rank it above `medium`. ``merchant-large`` is there to be the platform it
    is measured against, and stays healthy throughout.

    Long enough that the small merchant's normal hour is actually learnable -
    the point is a merchant-relative judgement, and a store too short to have a
    normal must fall back to dollars.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(large_per_minute):
            index += 1
            approved = rng.random() < 0.92
            events.append(
                _event(
                    index,
                    minute,
                    merchant_id="merchant-large",
                    provider="provider-p3",
                    country="MX",
                    issuing_bank="bank-y",
                    amount=large_amount,
                    status="approved" if approved else "declined",
                    **({} if approved else {"normalized_decline_reason": "insufficient_funds"}),
                )
            )
        for _ in range(small_per_minute):
            index += 1
            if minute < onset_minute:
                rate = 0.92
            else:
                # Worsening, not a step: the trajectory term is part of what
                # the dollar ceiling is suppressing, so the fixture has to
                # contain it rather than assume it.
                progress = (minute - onset_minute) / max(minutes - onset_minute, 1)
                rate = 0.40 - 0.30 * progress
            approved = rng.random() < rate
            events.append(
                _event(
                    index,
                    minute,
                    merchant_id="merchant-small",
                    provider="provider-p2",
                    country="CO",
                    issuing_bank="bank-x",
                    amount=small_amount,
                    status="approved" if approved else "declined",
                    **({} if approved else {"normalized_decline_reason": "do_not_honor"}),
                )
            )
    return events


def latency_degradation(
    minutes: int = 80,
    per_minute: int = 24,
    onset_minute: int = 70,
    seed: int = 13,
) -> list[dict[str, Any]]:
    """W1's `effect=latency` shape: the provider slows down, conversion does not.

    Attempts still approve and decline at baseline rates while `latency_ms`
    spikes, which is precisely the case conversion-only detection cannot see.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            base = _event(index, minute)
            degraded = minute >= onset_minute and base["provider"] == "provider-p2"
            approved = rng.random() < 0.92
            base["status"] = "approved" if approved else "declined"
            if not approved:
                base["normalized_decline_reason"] = "insufficient_funds"
            if degraded:
                base["latency_ms"] = rng.randint(6_000, 9_000)
                base["queue_delay_ms"] = 4_000
            events.append(base)
    return events


def provider_outage(
    minutes: int = 80,
    per_minute: int = 24,
    onset_minute: int = 70,
    seed: int = 17,
) -> list[dict[str, Any]]:
    """W1's `effect=outage` shape: the provider is routed around entirely.

    Its volume goes to zero rather than showing declines, so there is nothing
    in the decline mix to see and the surviving traffic looks perfectly
    healthy. A cohort with no attempts can never clear the volume floor, so
    conversion-only detection cannot even evaluate it.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            base = _event(index, minute)
            if minute >= onset_minute:
                base["provider"] = "provider-p3"
            approved = rng.random() < 0.92
            base["status"] = "approved" if approved else "declined"
            if not approved:
                base["normalized_decline_reason"] = "insufficient_funds"
            events.append(base)
    return events


def two_stage_deviation(
    minutes: int = 88,
    per_minute: int = 100,
    mild_minute: int = 79,
    collapse_minute: int = 83,
    mild_rate: float = 0.80,
    seed: int = 23,
) -> list[dict[str, Any]]:
    """The demo beat: a mild deviation first, then the hard collapse.

    From `mild_minute` provider-p2 gives up a dozen conversion points -
    worsening, operationally meaningful, and statistically suggestive without
    being conclusive, so it is a watch rather than an incident. From
    `collapse_minute` it falls off a cliff and crosses every detection floor.

    The mild step lands part-way through the detection window rather than
    before it, which is what the live demo actually does: a judge injects, a
    couple of minutes pass, and the sweep sees healthy buckets followed by
    degraded ones. That contrast inside the window is what makes trajectory
    read as worsening at all.

    Volume is deliberately realistic rather than minimal. Trajectory is
    measured over five one-minute buckets, and on a thin cohort the direction
    of a slide sits below the binomial noise floor - a fixture at twenty
    payments a bucket would be asserting on a coin toss.
    """
    rng = random.Random(seed)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(minutes):
        for _ in range(per_minute):
            index += 1
            base = _event(index, minute)
            affected = base["provider"] == "provider-p2"
            rate = 0.92
            if affected and minute >= collapse_minute:
                rate = 0.30
            elif affected and minute >= mild_minute:
                rate = mild_rate
            approved = rng.random() < rate
            base["status"] = "approved" if approved else "declined"
            if not approved:
                base["normalized_decline_reason"] = (
                    "do_not_honor" if affected else "insufficient_funds"
                )
            events.append(base)
    return events


def two_stage_deviation_mild_only(**kwargs: Any) -> list[dict[str, Any]]:
    """Only the first stage: the mild slide, before the collapse arrives."""
    collapse_minute = kwargs.get("collapse_minute", 83)
    cutoff = (BASE + timedelta(minutes=collapse_minute)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [event for event in two_stage_deviation(**kwargs) if event["occurred_at"] < cutoff]


# Live-vocabulary healthy history. The fixtures above stay on the detector's
# test names (provider-p2, bank-x, USD) so existing assertions keep passing.
# The demo workers emit a different vocabulary - merchant-b/adyen in COP on
# Colombian banks - and a baseline is per-cohort, so history written in the
# test names would leave the judge's cohort with no trailing window at all.
# PaymentAttemptBuilder cannot fill that gap: it stamps wall-clock now and
# sleeps 50ms per retry, which cannot produce backdated event time in seconds.
# This generator is the smallest bridge: canonical events, worker profiles
# for the live names, synthetic's seeded construction for time and rate.

LIVE_HISTORY_SEED = 20260830
DEMO_MERCHANT_ID = "merchant-b"
DEMO_PROVIDER = "adyen"
LIVE_HISTORY_HOURS = 8.0
LIVE_HISTORY_PER_MERCHANT_PER_MINUTE = 24
_HEALTHY_LATENCY_MS = 220


def _live_merchant_specs() -> tuple[dict[str, Any], ...]:
    """Read the live names from W1. Do not restate them here."""
    from worker.helpers.payment import CARD_NETWORKS, CURRENCY_RANGES
    from worker.profiles.merchant_a import PROFILE as merchant_a
    from worker.profiles.merchant_b import PROFILE as merchant_b
    from worker.profiles.merchant_c import PROFILE as merchant_c
    from worker.reference.banks import BANKS

    specs = []
    for profile in (merchant_a, merchant_b, merchant_c):
        specs.append(
            {
                "merchant_id": profile.merchant_id,
                "country": profile.country,
                "currency": profile.currency,
                "payment_methods": tuple(profile.payment_methods),
                "providers": tuple(profile.providers),
                "banks": tuple(BANKS[profile.country]),
                "amount_range": CURRENCY_RANGES[profile.currency],
                "card_networks": tuple(CARD_NETWORKS),
            }
        )
    return tuple(specs)


def _anchor(as_of: datetime) -> datetime:
    aware = as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    return aware.replace(second=0, microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minute_outcomes(count: int, decline_probability: float, rng: random.Random) -> list[bool]:
    """Exact decline count for one minute, shuffled. Bernoulli noise is what
    trips a watch on otherwise healthy history."""
    declines = int(round(count * decline_probability))
    declines = min(max(declines, 0), count)
    flags = [False] * declines + [True] * (count - declines)
    rng.shuffle(flags)
    return flags


def iter_live_healthy_history(
    *,
    hours: float | None = None,
    minutes: int | None = None,
    per_merchant_per_minute: int = LIVE_HISTORY_PER_MERCHANT_PER_MINUTE,
    seed: int = LIVE_HISTORY_SEED,
    as_of: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Healthy live-vocabulary attempts sitting immediately behind `as_of`.

    Event time, not wall-clock: history occupies the `hours` immediately
    behind and including the anchored `as_of`, so a stack that then starts
    publishing stitches onto this window rather than a hard-coded date.
    Randomness is seeded; the same `(as_of, seed, hours)` pair reproduces the
    same history. Conversion is held flat on purpose - this is context, not
    an incident.
    """
    if minutes is not None and hours is not None:
        raise ValueError("pass hours or minutes, not both")
    if minutes is None:
        span_hours = LIVE_HISTORY_HOURS if hours is None else hours
        if span_hours <= 0:
            raise ValueError("hours must be positive")
        minutes = int(round(span_hours * 60))
    if minutes < 1:
        raise ValueError("history must cover at least one minute")
    if per_merchant_per_minute < 1:
        raise ValueError("per_merchant_per_minute must be at least 1")

    from worker.helpers.payment import BASELINE_DECLINE_PROBABILITY

    rng = random.Random(seed)
    specs = _live_merchant_specs()
    end = _anchor(as_of or datetime.now(timezone.utc))
    index = 0
    # Inclusive of the anchor minute so `hours=6` is six hours of event time,
    # not 5h59m, which is below MERCHANT_NORMAL_MIN_HOURS. Live traffic for
    # that same minute is healthy too and INSERT OR IGNORE-dedupes on event_id.
    for minute in range(minutes + 1):
        minute_start = end - timedelta(minutes=minutes - minute)
        for spec in specs:
            outcomes = _minute_outcomes(
                per_merchant_per_minute, BASELINE_DECLINE_PROBABILITY, rng
            )
            providers = spec["providers"]
            methods = spec["payment_methods"]
            banks = spec["banks"]
            networks = spec["card_networks"]
            lo, hi = spec["amount_range"]
            for slot, approved in enumerate(outcomes):
                index += 1
                payment_method = methods[slot % len(methods)]
                occurred = minute_start + timedelta(
                    seconds=slot % 60, milliseconds=(index % 10) * 10
                )
                event = {
                    "event_id": f"hist-{seed}-{index:07d}",
                    "payment_id": f"pay-hist-{index:07d}",
                    "attempt_id": f"att-hist-{index:07d}-1",
                    "attempt_number": 1,
                    "occurred_at": _iso(occurred),
                    "merchant_id": spec["merchant_id"],
                    "provider": providers[slot % len(providers)],
                    "payment_method": payment_method,
                    "card_network": (
                        networks[slot % len(networks)] if payment_method == "card" else None
                    ),
                    "country": spec["country"],
                    "issuing_bank": banks[slot % len(banks)],
                    "status": "approved" if approved else "declined",
                    "amount": rng.randint(lo, hi) / 100.0,
                    "currency": spec["currency"],
                    "latency_ms": _HEALTHY_LATENCY_MS,
                }
                if not approved:
                    event["normalized_decline_reason"] = "insufficient_funds"
                yield event


def live_healthy_history(**kwargs: Any) -> list[dict[str, Any]]:
    """Materialise `iter_live_healthy_history` for tests that want a list."""
    return list(iter_live_healthy_history(**kwargs))
