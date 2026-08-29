"""Baseline, deviation test, localisation, severity and the C3 record.

The split that matters: this module decides *where* something changed and *how
much it costs*. It never decides *why*. There is deliberately no field in its
output for a cause, a hypothesis or a confidence.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from . import config, metrics, schema


# --------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------

def baseline_conversion(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Expected payment conversion for a cohort, from the trailing window.

    v0 deliberately uses a trailing window rather than an hour-of-week
    seasonal profile: the seasonal version needs backfill history that does not
    exist yet, and a baseline learned from the minutes before an incident is
    worse than an honest crude one.

    Low-volume cohorts are shrunk toward their parent, which is what stops an
    eight-payment cell from producing a wild expectation and screaming.
    """
    trailing_start = start - config.BASELINE_TRAILING_BUCKETS * config.BUCKET_SECONDS
    observed = metrics.payment_metrics(connection, cohort, trailing_start, start)
    parent = metrics.payment_metrics(connection, None, trailing_start, start)

    n = observed["attempted_payments"]
    parent_rate = parent["approval_conversion"]
    if n == 0:
        return {
            "expected": parent_rate,
            "method": "parent_only",
            "trailing_payments": 0,
            "trailing_start_epoch": trailing_start,
        }

    cohort_rate = observed["approval_conversion"]
    if parent_rate is None:
        return {
            "expected": cohort_rate,
            "method": "cohort_only",
            "trailing_payments": n,
            "trailing_start_epoch": trailing_start,
        }

    weight = n / (n + config.SHRINKAGE_PRIOR_PAYMENTS)
    return {
        "expected": weight * cohort_rate + (1 - weight) * parent_rate,
        "method": "trailing_window_with_parent_shrinkage",
        "trailing_payments": n,
        "shrinkage_weight": round(weight, 6),
        "trailing_start_epoch": trailing_start,
    }


# --------------------------------------------------------------------------
# deviation
# --------------------------------------------------------------------------

def two_proportion_z(
    observed_rate: float,
    observed_n: int,
    expected_rate: float,
    expected_n: int,
) -> float | None:
    """Signed z for observed versus expected conversion. Negative is a drop."""
    if observed_n <= 0 or expected_n <= 0:
        return None
    pooled = (observed_rate * observed_n + expected_rate * expected_n) / (observed_n + expected_n)
    if pooled <= 0.0 or pooled >= 1.0:
        return None
    standard_error = math.sqrt(pooled * (1 - pooled) * (1 / observed_n + 1 / expected_n))
    if standard_error == 0:
        return None
    return (observed_rate - expected_rate) / standard_error


def evaluate(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Measure one cohort against its baseline and apply the four floors.

    Every floor rejects a specific false positive, and each is reported so a
    near-miss can be explained rather than merely not happening.
    """
    observed = metrics.payment_metrics(connection, cohort, start, end)
    base = baseline_conversion(connection, cohort, start, end)
    expected = base["expected"]
    actual = observed["approval_conversion"]

    result: dict[str, Any] = {
        "cohort": dict(cohort or {}),
        "cohort_key": metrics.cohort_key(cohort),
        "observed": observed,
        "baseline": base,
        "expected": expected,
        "actual": actual,
        "z": None,
        "absolute_drop": None,
        "qualifies": False,
        "floors": {},
    }
    if expected is None or actual is None or observed["attempted_payments"] == 0:
        result["floors"] = {"has_measurement": False}
        return result

    drop = expected - actual
    z = two_proportion_z(
        actual,
        observed["attempted_payments"],
        expected,
        max(base.get("trailing_payments", 0), 1),
    )
    floors = {
        "has_measurement": True,
        "z_min": bool(z is not None and z <= -config.Z_MIN),
        "absolute_drop_min": bool(drop >= config.ABS_DROP_MIN),
        "volume_min": bool(observed["attempted_payments"] >= config.N_PAYMENTS_MIN),
    }
    result.update(
        {
            "z": z,
            "absolute_drop": drop,
            "floors": floors,
            "qualifies": all(floors.values()),
        }
    )
    return result


# --------------------------------------------------------------------------
# localisation
# --------------------------------------------------------------------------

def localise(
    connection: sqlite3.Connection,
    start: int,
    end: int,
    seed: dict[str, Any] | None = None,
    dimensions: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Walk from the whole platform down to the cohort the evidence supports.

    The rule is contrast, not depth. We descend on a dimension only when one of
    its values is materially worse than its siblings inside the current cohort.
    If every issuing bank behind a degraded provider is equally degraded, the
    issuer is not part of the story and adding it would be a coincidence
    dressed up as a diagnosis - which is exactly the over-specification PRD
    section 12 warns against.

    Nothing about any particular path is encoded, only the rule for descending
    it, so a dimension combination nobody declared can still be located.

    ``dimensions`` narrows which dimensions may be descended on, for a caller
    that asked for a specific path. It never widens the space: an unknown name
    is rejected rather than quietly ignored.
    """
    considered = tuple(dimensions) if dimensions is not None else schema.DIMENSIONS
    for dimension in considered:
        if dimension not in schema.DIMENSIONS:
            raise ValueError(f"{dimension!r} is not a cohort dimension")

    path: list[dict[str, Any]] = []
    current = dict(seed or {})
    path.append(evaluate(connection, current or None, start, end))

    for _ in range(config.LOCALISE_MAX_DEPTH):
        best_split: dict[str, Any] | None = None

        for dimension in (d for d in considered if d not in current):
            siblings = []
            for row in connection.execute(
                f"SELECT DISTINCT {dimension} AS v FROM attempt "
                f"WHERE {dimension} IS NOT NULL ORDER BY {dimension}"
            ).fetchall():
                child = dict(current)
                child[dimension] = row["v"]
                evaluation = evaluate(connection, child, start, end)
                if evaluation["absolute_drop"] is None:
                    continue
                if evaluation["observed"]["attempted_payments"] < config.N_PAYMENTS_MIN:
                    continue
                siblings.append(evaluation)

            # One value is no contrast: the child would just be the parent
            # wearing an extra label.
            if len(siblings) < 2:
                continue

            siblings.sort(key=lambda item: (-item["absolute_drop"], item["cohort_key"]))
            separation = siblings[0]["absolute_drop"] - siblings[1]["absolute_drop"]
            if best_split is None or separation > best_split["separation"]:
                best_split = {
                    "separation": separation,
                    "winner": siblings[0],
                    "runner_up": siblings[1],
                    "dimension": dimension,
                }

        if best_split is None or best_split["separation"] < config.LOCALISE_MIN_SEPARATION:
            break

        winner = best_split["winner"]
        winner["split"] = {
            "dimension": best_split["dimension"],
            "separation_from_next": round(best_split["separation"], 6),
            "runner_up": best_split["runner_up"]["cohort_key"],
        }
        path.append(winner)
        current = dict(winner["cohort"])

    return path


# --------------------------------------------------------------------------
# severity
# --------------------------------------------------------------------------

def severity_of(
    loss_per_hour: float,
    affected_payments: int,
    platform_payments: int,
    buckets_sustained: int,
    trajectory: int,
) -> dict[str, Any]:
    """Business priority. Statistical strength is deliberately not an input.

    Money is log-scaled so that a $25,000/hour incident outranks a $120/hour
    one decisively without outranking it two-hundred-fold, which is what keeps
    a large merchant's small percentage shift above a tiny cohort's dramatic
    one without hand-tuning either case.
    """
    floor = config.LOSS_RATE_FLOOR_USD_PER_HOUR
    cap = config.LOSS_RATE_CAP_USD_PER_HOUR
    scaled = max(0.0, loss_per_hour) / floor
    impact = min(1.0, math.log10(1 + scaled) / math.log10(1 + cap / floor))
    radius = (affected_payments / platform_payments) if platform_payments else 0.0
    persistence = min(1.0, buckets_sustained / config.PERSISTENCE_FULL_BUCKETS)
    components = {
        "impact": round(impact, 6),
        "radius": round(min(1.0, radius), 6),
        "persistence": round(persistence, 6),
        "trajectory": max(0, trajectory),
    }
    score = sum(config.SEVERITY_WEIGHTS[name] * components[name] for name in components)
    label = config.SEVERITY_FLOOR
    for candidate, threshold in config.SEVERITY_THRESHOLDS:
        if score >= threshold:
            label = candidate
            break

    # Apply the money ceiling. Without it a long, worsening, wide but cheap
    # incident can climb on persistence and trajectory alone.
    order = [config.SEVERITY_FLOOR] + [name for name, _ in reversed(config.SEVERITY_THRESHOLDS)]
    ceiling = None
    for limit, band in config.SEVERITY_LOSS_RATE_CEILING:
        if loss_per_hour < limit:
            ceiling = band
            break
    if ceiling is not None and order.index(label) > order.index(ceiling):
        label = ceiling

    return {
        "severity": label,
        "severity_score": round(score, 6),
        "components": components,
        "loss_rate_ceiling": ceiling,
    }


def trajectory_of(series: list[dict[str, Any]]) -> int:
    """+1 worsening, 0 flat, -1 recovering, from the tail of a series."""
    rates = [point["approval_conversion"] for point in series if point["approval_conversion"] is not None]
    if len(rates) < 4:
        return 0
    half = len(rates) // 2
    earlier = sum(rates[:half]) / half
    later = sum(rates[half:]) / (len(rates) - half)
    if later < earlier - 0.01:
        return 1
    if later > earlier + 0.01:
        return -1
    return 0


# --------------------------------------------------------------------------
# C3 record
# --------------------------------------------------------------------------

def _is_degraded(point: dict[str, Any], expected: float) -> bool:
    """One bucket qualifies when it is measured and drops by at least the floor."""
    actual = point["approval_conversion"]
    return actual is not None and expected - actual >= config.ABS_DROP_MIN


def _episode_extent(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
    expected: float,
    series: list[dict[str, Any]],
) -> tuple[int, int]:
    """First observed time of this deviation, and how many buckets it has held.

    ``docs/contracts/incident.md`` defines onset as the first observed time of
    the qualifying deviation. Scanning only the detection window cannot honour
    that: the window is the last few buckets, so ``min(degraded)`` over it
    returns the window start whenever the deviation began before the sweep, and
    a degradation that has run for an hour reports as one that began minutes
    ago. ``buckets_sustained`` was capped the same way, so persistence - a
    severity term - saturated at the window length and under-ranked exactly the
    incidents that had been costing money longest.

    So the run is walked backwards from the window, bucket by bucket, while it
    stays degraded, bounded by the same trailing span the baseline already
    reads. The walk stops at the first bucket that is not degraded, which keeps
    onset the start of *this* episode rather than the start of any earlier dip
    that has since recovered.
    """
    degraded = [p["bucket_start_epoch"] for p in series if _is_degraded(p, expected)]
    if not degraded:
        return start, 0

    onset = min(degraded)
    sustained = len(degraded)
    if onset > start:
        # The deviation begins inside the window, so the window already saw it start.
        return onset, sustained

    lookback = start - config.BASELINE_TRAILING_BUCKETS * config.BUCKET_SECONDS
    if lookback >= start:
        return onset, sustained

    earlier = metrics.timeseries(connection, cohort, lookback, start)
    for point in reversed(earlier):
        if not _is_degraded(point, expected):
            break
        onset = point["bucket_start_epoch"]
        sustained += 1
    return onset, sustained


def build_incident(
    connection: sqlite3.Connection,
    start: int,
    end: int,
    incident_id: str = "inc-0001",
) -> dict[str, Any] | None:
    """Produce one C3 record for the strongest qualifying cohort, or None.

    Returning None on a quiet window is the point: not firing on noise is a
    graded behaviour, not an absence of work.
    """
    path = localise(connection, start, end)
    qualifying = [step for step in path if step["qualifies"]]
    if not qualifying:
        return None

    reported = qualifying[-1]
    cohort = reported["cohort"]

    impact = metrics.financial_impact(connection, cohort or None, start, end, reported["expected"])
    series = metrics.timeseries(connection, cohort or None, start, end)
    platform = metrics.payment_metrics(connection, None, start, end)
    attempts = metrics.attempt_metrics(connection, cohort or None, start, end)
    retries = metrics.retry_profile(connection, cohort or None, start, end)

    onset, buckets_sustained = _episode_extent(
        connection, cohort or None, start, end, reported["expected"], series
    )

    severity = severity_of(
        loss_per_hour=impact["loss_per_hour"]["amount"],
        affected_payments=reported["observed"]["attempted_payments"],
        platform_payments=platform["attempted_payments"],
        buckets_sustained=buckets_sustained,
        trajectory=trajectory_of(series),
    )

    blast = metrics.blast_radius(connection, cohort or None, start, end)

    return {
        "incident_id": incident_id,
        "affected_cohort": cohort,
        "change": {
            "metric": "payment_approval_conversion",
            "expected": round(reported["expected"], 6),
            "actual": round(reported["actual"], 6),
            "absolute_delta": round(reported["actual"] - reported["expected"], 6),
            "relative_change": (
                round((reported["actual"] - reported["expected"]) / reported["expected"], 6)
                if reported["expected"]
                else None
            ),
            "unit": "ratio",
        },
        "onset": schema.iso_utc(onset),
        "persistence": {
            "is_persistent": buckets_sustained >= config.SUSTAIN_BUCKETS,
            "observed_for_seconds": buckets_sustained * config.BUCKET_SECONDS,
            "last_observed_at": schema.iso_utc(end),
        },
        "blast_radius": blast,
        "financial_impact": impact,
        "severity": severity["severity"],
        "lifecycle_state": "detected",
        # Everything below is W2 provenance: not part of the published C3 field
        # set, and safe for any consumer to ignore.
        "detection": {
            "config_version": config.CONFIG_VERSION,
            # The unrounded expectation and the exact measured window, so a C2
            # tool asked about this incident later recomputes the identical
            # number rather than a re-rounded approximation of it.
            "expected_conversion": reported["expected"],
            "window": {"start_epoch": start, "end_epoch": end},
            "severity_score": severity["severity_score"],
            "severity_components": severity["components"],
            "z": round(reported["z"], 4) if reported["z"] is not None else None,
            "baseline_method": reported["baseline"]["method"],
            "buckets_sustained": buckets_sustained,
            "attempt_approval_conversion": attempts["approval_conversion"],
            "retry_amplification_factor": retries["retry_amplification_factor"],
            "localisation_path": [
                {
                    "cohort_key": step["cohort_key"],
                    "z": round(step["z"], 4) if step["z"] is not None else None,
                    "qualifies": step["qualifies"],
                }
                for step in path
            ],
        },
    }
