"""Baseline, deviation test, localisation, severity and the C3 record.

The split that matters: this module decides *where* something changed and *how
much it costs*. It never decides *why*. There is deliberately no field in its
output for a cause, a hypothesis or a confidence.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from . import config, metrics, schema, store


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

    Contrast is the normal descent rule: one value must be materially worse
    than its siblings inside the current cohort. When a parent does not pass
    the detection floors, a unique child that does pass them is also a valid
    localisation - dilution by healthy traffic must not hide a real incident.
    When a qualified parent has only one observed value for a dimension that
    varies elsewhere in the window, that observed singleton may be retained as
    part of the joint affected slice. It records what the data contains, not a
    causal claim about that dimension; the investigation layer still tests
    whether the dimensions are structurally inseparable.

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
        children: dict[str, list[dict[str, Any]]] = {}
        global_value_counts: dict[str, int] = {}

        for dimension in (d for d in considered if d not in current):
            values = connection.execute(
                f"SELECT DISTINCT {dimension} AS v FROM attempt "
                f"WHERE {dimension} IS NOT NULL ORDER BY {dimension}"
            ).fetchall()
            global_value_counts[dimension] = len(values)
            siblings = []
            for row in values:
                child = dict(current)
                child[dimension] = row["v"]
                evaluation = evaluate(connection, child, start, end)
                if evaluation["absolute_drop"] is None:
                    continue
                if evaluation["observed"]["attempted_payments"] < config.N_PAYMENTS_MIN:
                    continue
                siblings.append(evaluation)
            children[dimension] = siblings

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

        if best_split is not None and best_split["separation"] >= config.LOCALISE_MIN_SEPARATION:
            winner = best_split["winner"]
            winner["split"] = {
                "dimension": best_split["dimension"],
                "separation_from_next": round(best_split["separation"], 6),
                "runner_up": best_split["runner_up"]["cohort_key"],
            }
            path.append(winner)
            current = dict(winner["cohort"])
            continue

        # A parent can be diluted below the floors while a child still has a
        # real, persistent signal. Descend only to the unique qualifying child
        # for a dimension, then choose the strongest such child deterministically.
        if not path[-1]["qualifies"]:
            qualifying_children = [
                (dimension, child)
                for dimension, siblings in children.items()
                if sum(item["qualifies"] for item in siblings) == 1
                for child in siblings
                if child["qualifies"]
            ]
            if qualifying_children:
                dimension, winner = min(
                    qualifying_children,
                    key=lambda item: (-item[1]["absolute_drop"], item[1]["cohort_key"]),
                )
                winner["split"] = {
                    "dimension": dimension,
                    "kind": "qualifying_child",
                }
                path.append(winner)
                current = dict(winner["cohort"])
                continue

        # A one-to-one confounder has no sibling inside the qualified parent,
        # but the singleton value is still the observed affected joint slice.
        # Only retain it when that dimension has another value elsewhere, so a
        # globally constant field is never added as decorative specificity.
        if path[-1]["qualifies"]:
            singleton_children = [
                (dimension, siblings[0])
                for dimension, siblings in children.items()
                if global_value_counts[dimension] > 1 and len(siblings) == 1
                and siblings[0]["qualifies"]
            ]
            if singleton_children:
                dimension, winner = min(
                    singleton_children,
                    key=lambda item: (-item[1]["absolute_drop"], item[1]["cohort_key"]),
                )
                winner["split"] = {
                    "dimension": dimension,
                    "kind": "observed_singleton",
                }
                path.append(winner)
                current = dict(winner["cohort"])
                continue

        break

    return path


# --------------------------------------------------------------------------
# severity
# --------------------------------------------------------------------------

SEVERITY_ORDER = [config.SEVERITY_FLOOR] + [
    name for name, _ in reversed(config.SEVERITY_THRESHOLDS)
]


def _ladder_band(value: float, ladder: tuple[tuple[float, str], ...]) -> str | None:
    """The band this value is capped at, or None when it is above every rung."""
    for limit, band in ladder:
        if value < limit:
            return band
    return None


def merchant_normal_hourly_value(connection: sqlite3.Connection) -> dict[str, float]:
    """Each merchant's normal attempted value per hour, from the whole store.

    One aggregate, computed once per sweep rather than once per candidate
    cohort. Value is taken per payment, never per attempt, for the same reason
    `financial_impact` does: a retry storm must not inflate the denominator
    exactly when it matters most.

    A merchant only gets a normal when there is enough history to call it one.
    Below either floor it is absent from the result, and severity falls back to
    the dollars-only ladder - which is today's behaviour unchanged, so a short
    or empty store cannot move a single existing number.
    """
    rows = connection.execute(
        """
        SELECT merchant_id,
               COUNT(*) AS payments,
               SUM(value) AS attempted_value,
               MIN(first_epoch) AS lo,
               MAX(first_epoch) AS hi
        FROM (
            SELECT merchant_id,
                   payment_id,
                   MAX(amount_usd) AS value,
                   MIN(occurred_epoch) AS first_epoch
            FROM attempt
            GROUP BY merchant_id, payment_id
        )
        GROUP BY merchant_id
        """
    ).fetchall()
    normals: dict[str, float] = {}
    for row in rows:
        payments = int(row["payments"] or 0)
        hours = (int(row["hi"]) - int(row["lo"])) / 3600.0 if payments else 0.0
        if payments < config.MERCHANT_NORMAL_MIN_PAYMENTS:
            continue
        if hours < config.MERCHANT_NORMAL_MIN_HOURS:
            continue
        normals[row["merchant_id"]] = float(row["attempted_value"] or 0.0) / hours
    return normals


def prior_matching_incident_count(
    connection: sqlite3.Connection,
    cohort_key: str,
    onset_epoch: int,
    lookback_seconds: int = config.RECURRENCE_LOOKBACK_SECONDS,
    episode_gap_seconds: int = config.RECURRENCE_EPISODE_GAP_SECONDS,
) -> int:
    """How many prior *episodes* on this exact cohort fall inside the lookback.

    Episodes, not rows. Onset is measured from the rolling detect window and the
    incident id is derived from onset, so one continuous fault drifts into
    several rows; counting rows would let a single prior rehearsal meet the
    two-prior promotion threshold. A row counts only when the cohort was quiet
    between it and this incident - `last_seen_epoch` at least
    `episode_gap_seconds` before `onset_epoch`.

    A `watching` row never counts: it is a near-miss we chose not to page on.
    Every other lifecycle state does, because an incident that was investigated,
    diagnosed, mitigated or resolved is a genuine prior recurrence.

    This is NOT the figure `incident_history` publishes as
    `recurrence.prior_matching_incidents`. That one is a plain count of the rows
    it lists over an operator-chosen window and is unchanged by this function.
    """
    excluded = config.RECURRENCE_EXCLUDED_LIFECYCLE_STATES
    placeholders = ",".join("?" for _ in excluded)
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM incident "
        "WHERE cohort_key = ? AND onset_epoch >= ? AND onset_epoch < ? "
        f"AND last_seen_epoch <= ? AND lifecycle_state NOT IN ({placeholders})",
        (
            cohort_key,
            onset_epoch - lookback_seconds,
            onset_epoch,
            onset_epoch - episode_gap_seconds,
            *excluded,
        ),
    ).fetchone()
    return int(row["n"] or 0)


def severity_of(
    loss_per_hour: float,
    affected_payments: int,
    platform_payments: int,
    buckets_sustained: int,
    trajectory: int,
    prior_matching_incidents: int = 0,
    loss_share_of_normal: float | None = None,
) -> dict[str, Any]:
    """Business priority. Statistical strength is deliberately not an input.

    Money is log-scaled so that a $25,000/hour incident outranks a $120/hour
    one decisively without outranking it two-hundred-fold, which is what keeps
    a large merchant's small percentage shift above a tiny cohort's dramatic
    one without hand-tuning either case.

    Two inputs default to today's behaviour so no existing caller changes:
    `prior_matching_incidents` is 0 - nothing has recurred - and
    `loss_share_of_normal` is None - this merchant's normal hour is unknown,
    so only the dollar ceiling applies.
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
    order = SEVERITY_ORDER
    dollar_ceiling = _ladder_band(loss_per_hour, config.SEVERITY_LOSS_RATE_CEILING)

    # The same loss read against this merchant's own normal hour. The effective
    # ceiling is whichever of the two bands is HIGHER, so a proportionally
    # catastrophic loss on a small merchant is no longer capped by a ladder
    # written for a large one. A None from either side means that ladder caps
    # nothing at all, which is the highest answer there is.
    share_ceiling = (
        _ladder_band(loss_share_of_normal, config.SEVERITY_LOSS_SHARE_CEILING)
        if loss_share_of_normal is not None
        else None
    )
    if loss_share_of_normal is None:
        ceiling = dollar_ceiling
    elif dollar_ceiling is None or share_ceiling is None:
        ceiling = None
    else:
        ceiling = max(dollar_ceiling, share_ceiling, key=order.index)

    if ceiling is not None and order.index(label) > order.index(ceiling):
        label = ceiling

    # Recurrence lifts, after the ceilings have capped. Same machinery, other
    # direction: a fault that has already fired on this cohort twice inside the
    # lookback is a worse fault than one that has fired once, and the ladder
    # keeps saying so until it runs out at critical.
    promotion = 0
    for count, steps in config.SEVERITY_RECURRENCE_PROMOTION:
        if prior_matching_incidents >= count:
            promotion = steps
    if promotion:
        label = order[min(len(order) - 1, order.index(label) + promotion)]

    return {
        "severity": label,
        "severity_score": round(score, 6),
        "components": components,
        "loss_rate_ceiling": dollar_ceiling,
        "loss_share_ceiling": share_ceiling,
        "loss_share_of_merchant_normal": (
            round(loss_share_of_normal, 6) if loss_share_of_normal is not None else None
        ),
        "effective_ceiling": ceiling,
        "prior_matching_incidents": prior_matching_incidents,
        "recurrence_promotion_bands": promotion,
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


def _c3_record(
    connection: sqlite3.Connection,
    path: list[dict[str, Any]],
    reported: dict[str, Any],
    start: int,
    end: int,
    incident_id: str,
    lifecycle_state: str,
    merchant_normals: dict[str, float] | None,
    watch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one C3 record for a cohort, detected or watching.

    One builder for both states on purpose: a watch and the incident it becomes
    are the same record for the same cohort (DECISIONS.md, 2026-08-30T03:59Z),
    so building them from two code paths would be two shapes that drift. The
    only differences are the lifecycle state, the severity a watch is forced
    to, and the `detection.watch` block explaining why we are not yet convinced.
    """
    cohort = reported["cohort"]
    actual = reported["actual"]
    typical_hourly = metrics.typical_hourly_attempted_value(connection, cohort or None, start)
    impact = metrics.financial_impact(
        connection, cohort or None, start, end, reported["expected"], typical_hourly
    )
    series = metrics.timeseries(connection, cohort or None, start, end)
    platform = metrics.payment_metrics(connection, None, start, end)
    attempts = metrics.attempt_metrics(connection, cohort or None, start, end)
    retries = metrics.retry_profile(connection, cohort or None, start, end)
    trajectory = trajectory_of(series)

    onset, buckets_sustained = _episode_extent(
        connection, cohort or None, start, end, reported["expected"], series
    )

    # Both new severity inputs are measured, never estimated: one is a COUNT
    # over the incident table, the other an aggregate over attempt. The
    # merchant normals are computed once for the sweep and handed in.
    merchant = cohort.get("merchant_id")
    normals = (
        merchant_normals
        if merchant_normals is not None
        else merchant_normal_hourly_value(connection)
    )
    normal_hourly = normals.get(merchant) if merchant else None
    loss_per_hour = impact["loss_per_hour"]["amount"]
    loss_share = (loss_per_hour / normal_hourly) if normal_hourly else None

    severity = severity_of(
        loss_per_hour=loss_per_hour,
        affected_payments=reported["observed"]["attempted_payments"],
        platform_payments=platform["attempted_payments"],
        buckets_sustained=buckets_sustained,
        trajectory=trajectory,
        prior_matching_incidents=prior_matching_incident_count(
            connection, reported["cohort_key"], onset
        ),
        loss_share_of_normal=loss_share,
    )

    # A watch is forced to `low` regardless of what the components say. C5
    # routes on severity alone, so this is what makes it structurally
    # impossible for a warning to reach Slack or a phone even if somebody
    # later points escalation at the row by mistake.
    severity_label = "low" if watch is not None else severity["severity"]

    record = {
        "incident_id": incident_id,
        "affected_cohort": cohort,
        "change": {
            "metric": "payment_approval_conversion",
            "expected": round(reported["expected"], 6),
            # A cohort routed around entirely has an expectation and no
            # measurement at all, which is the signal rather than a gap. The
            # delta stays null rather than being invented as a zero.
            "actual": round(actual, 6) if actual is not None else None,
            "absolute_delta": (
                round(actual - reported["expected"], 6) if actual is not None else None
            ),
            "relative_change": (
                round((actual - reported["expected"]) / reported["expected"], 6)
                if actual is not None and reported["expected"]
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
        "blast_radius": metrics.blast_radius(connection, cohort or None, start, end),
        "financial_impact": impact,
        "severity": severity_label,
        "lifecycle_state": lifecycle_state,
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
            "severity_ceilings": {
                "loss_rate": severity["loss_rate_ceiling"],
                "merchant_relative": severity["loss_share_ceiling"],
                "effective": severity["effective_ceiling"],
            },
            "merchant_normal_hourly_value_usd": (
                round(normal_hourly, 2) if normal_hourly else None
            ),
            "loss_share_of_merchant_normal": severity["loss_share_of_merchant_normal"],
            "prior_matching_incidents": severity["prior_matching_incidents"],
            "recurrence_promotion_bands": severity["recurrence_promotion_bands"],
            "z": round(reported["z"], 4) if reported["z"] is not None else None,
            "baseline_method": reported["baseline"]["method"],
            "buckets_sustained": buckets_sustained,
            "trajectory": trajectory,
            "detection_floors": reported["floors"],
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
    if watch is not None:
        record["detection"]["watch"] = watch
        # The severity the components would have produced is kept for the
        # record, clearly not as the severity. Nothing routes on it.
        record["detection"]["unforced_severity"] = severity["severity"]
    return record


def build_incident(
    connection: sqlite3.Connection,
    start: int,
    end: int,
    incident_id: str = "inc-0001",
    merchant_normals: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Produce one C3 record for the strongest qualifying cohort, or None.

    Returning None on a quiet window is the point: not firing on noise is a
    graded behaviour, not an absence of work.
    """
    path = localise(connection, start, end)
    qualifying = [step for step in path if step["qualifies"]]
    if not qualifying:
        return None
    return _c3_record(
        connection, path, qualifying[-1], start, end, incident_id, "detected", merchant_normals
    )


# --------------------------------------------------------------------------
# the watch: a near-miss and the signals that move before conversion does
# --------------------------------------------------------------------------
#
# Detection today emits silence or a crossed-floor incident. A developing
# deviation is measured and then discarded, which is why the first thing a
# merchant hears about a degradation is the cliff. A watch is that near-miss,
# persisted as `lifecycle_state: watching` on the same C3 record the cohort
# will keep if it becomes an incident (DECISIONS.md, 2026-08-30T03:59Z). One
# cohort, one record: the warning and the incident are the same row, which is
# what lets the demo point at a timestamp.
#
# A watch is not an incident. Its severity is forced to `low` so C5 cannot
# page, `detected` remains the sole handoff signal, and the investigation
# daemon's claim SQL asks for `detected` and therefore never sees a watch.
#
# There are two ways in, and both are the comparison this plane already makes:
#
#   1. Conversion is deviating but has not crossed its floors - derek's
#      near-miss predicate, sitting beside the existing four floors.
#   2. The leading indicators have moved. A payment provider does not fail
#      instantly: latency rises, timeouts appear in the decline mix, retries
#      amplify, queues build, and conversion falls last. This is the same
#      trailing-baseline comparison pointed at different columns (ADR 0024).
#      Nothing is trained, fitted or forecast, and it never states a future
#      number.
#
# Two shapes W1 can produce are invisible to conversion alone, and route 2 is
# the only thing that sees either:
#   * effect=latency - attempts still approve and decline at baseline rates
#     while latency and queue delay spike, so conversion never moves;
#   * effect=outage - the provider is routed around entirely, so its volume
#     goes to zero rather than showing declines, and a cohort with no traffic
#     can never clear N_PAYMENTS_MIN to be evaluated at all.

FORMING_INDICATORS = ("timeout_share", "mean_latency_ms", "volume_rate")


def watch_floors(evaluation: dict[str, Any], trajectory: int) -> dict[str, Any]:
    """The near-miss predicate, in the same shape as the four detection floors.

    Every clause is reported whether or not it held, for exactly the reason
    `evaluate` reports its own: a warning that did not fire should be as
    explainable as one that did, and the vector is what a TAM reads to see why
    we are not yet convinced.
    """
    z = evaluation["z"]
    drop = evaluation["absolute_drop"]
    floors = {
        "has_measurement": bool(evaluation["floors"].get("has_measurement")),
        "not_already_an_incident": not evaluation["qualifies"],
        "volume_min": bool(evaluation["floors"].get("volume_min")),
        # Both clauses, not either. The suggested predicate offered the drop as
        # an alternative to the z-score; measured against the actual fixtures
        # that made the z-score inert, because ordinary minute-to-minute noise
        # on a healthy cohort clears a one-point drop routinely - a 92% cohort
        # sat at z -0.81 with a 3.4-point drop and would have been watched. The
        # z-score is what separates a real developing deviation from noise, and
        # the drop is what keeps a statistically clean but operationally
        # meaningless wobble out. Requiring both is what makes z -2.3 watch and
        # z -1.0 not, which was the binding tuning target.
        "statistically_real": bool(z is not None and z <= config.WATCH_Z_MAX),
        "materially_large": bool(drop is not None and drop >= config.WATCH_ABS_DROP_MIN),
        "worsening": trajectory == config.WATCH_TRAJECTORY,
    }
    return floors


def _watch_block(
    reasons: list[str],
    floors: dict[str, Any],
    trajectory: int,
    indicators: dict[str, Any] | None,
    degraded: list[str],
) -> dict[str, Any]:
    """Why this cohort is watched and not yet reported as an incident."""
    return {
        "reasons": reasons,
        "watch_floors": floors,
        "not_yet_met": [name for name, held in floors.items() if not held],
        "trajectory": trajectory,
        "leading_indicators": indicators,
        "degraded_leading_indicators": degraded,
        "statement": (
            "This cohort is unusual for itself against its last hour and is getting worse. "
            "It has not crossed the detection floors, so it is watched rather than paged. "
            "Nothing here is trained, fitted or forecast, and no future number is claimed."
        ),
    }


def leading_indicators(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Timeout share, latency and volume for a cohort, each against its own
    trailing baseline - the same window `baseline_conversion` reads.

    Every indicator reports its baseline, its observed value, the criterion it
    was judged by and whether it is degraded, so a signal that fires and one
    that does not are equally explainable.
    """
    trailing_start = start - config.BASELINE_TRAILING_BUCKETS * config.BUCKET_SECONDS
    window_buckets = max((end - start) / config.BUCKET_SECONDS, 1e-9)

    now = metrics.attempt_pressure(connection, cohort, start, end)
    base = metrics.attempt_pressure(connection, cohort, trailing_start, start)
    now_payments = metrics.payment_metrics(connection, cohort, start, end)
    base_payments = metrics.payment_metrics(connection, cohort, trailing_start, start)

    # The detector's own volume floor, applied to the recent side: below it a
    # share is arithmetic on too few attempts to defend.
    has_volume = now["attempts"] >= config.N_PAYMENTS_MIN

    timeout_delta = (
        now["timeout_share"] - base["timeout_share"]
        if now["timeout_share"] is not None and base["timeout_share"] is not None
        else None
    )
    latency_ratio = (
        now["mean_latency_ms"] / base["mean_latency_ms"]
        if now["mean_latency_ms"] is not None
        and base["mean_latency_ms"] is not None
        and base["mean_latency_ms"] >= config.FORMING_LATENCY_MIN_BASELINE_MS
        else None
    )

    now_rate = now_payments["attempted_payments"] / window_buckets
    base_rate = base_payments["attempted_payments"] / config.BASELINE_TRAILING_BUCKETS
    volume_ratio = (now_rate / base_rate) if base_rate else None
    # Volume collapse is judged on the *baseline* having been substantial, not
    # on the recent side clearing a floor: zero traffic is the signal itself,
    # and a floor on the recent side would make an outage unreportable.
    volume_judgeable = base_payments["attempted_payments"] >= config.FORMING_VOLUME_BASELINE_MIN
    # Latency and timeout need the two windows to be the same kind of traffic.
    # A 30x rate jump against a thin trailing hour is warmup (or a replay from
    # offset zero), not a forming outage.
    volume_comparable = bool(
        volume_ratio is not None
        and config.FORMING_VOLUME_COMPARABLE_MIN
        <= volume_ratio
        <= config.FORMING_VOLUME_COMPARABLE_MAX
    )

    return {
        "cohort": dict(cohort or {}),
        "cohort_key": metrics.cohort_key(cohort),
        "baseline_window": {"start_epoch": trailing_start, "end_epoch": start},
        "indicators": {
            "timeout_share": {
                "baseline": base["timeout_share"],
                "observed": now["timeout_share"],
                "delta": timeout_delta,
                "observed_attempts": now["attempts"],
                "criterion": (
                    f"timeout share rises by at least {config.FORMING_TIMEOUT_SHARE_DELTA} "
                    f"over the trailing baseline, on at least {config.N_PAYMENTS_MIN} attempts, "
                    f"with recent volume between {config.FORMING_VOLUME_COMPARABLE_MIN}x and "
                    f"{config.FORMING_VOLUME_COMPARABLE_MAX}x the trailing rate"
                ),
                "degraded": bool(
                    has_volume
                    and volume_comparable
                    and timeout_delta is not None
                    and timeout_delta >= config.FORMING_TIMEOUT_SHARE_DELTA
                ),
            },
            "mean_latency_ms": {
                "baseline": base["mean_latency_ms"],
                "observed": now["mean_latency_ms"],
                "ratio": round(latency_ratio, 6) if latency_ratio is not None else None,
                "criterion": (
                    f"mean latency reaches {config.FORMING_LATENCY_P95_RATIO}x a trailing "
                    f"baseline of at least {config.FORMING_LATENCY_MIN_BASELINE_MS}ms, "
                    f"on at least {config.N_PAYMENTS_MIN} attempts, with recent volume "
                    f"between {config.FORMING_VOLUME_COMPARABLE_MIN}x and "
                    f"{config.FORMING_VOLUME_COMPARABLE_MAX}x the trailing rate"
                ),
                "degraded": bool(
                    has_volume
                    and volume_comparable
                    and latency_ratio is not None
                    and latency_ratio >= config.FORMING_LATENCY_P95_RATIO
                ),
            },
            "volume_rate": {
                "baseline": round(base_rate, 6),
                "observed": round(now_rate, 6),
                "ratio": round(volume_ratio, 6) if volume_ratio is not None else None,
                "unit": "payments per bucket",
                "criterion": (
                    f"payment rate falls below {config.FORMING_VOLUME_COLLAPSE_RATIO} of its own "
                    f"trailing rate, after at least {config.FORMING_VOLUME_BASELINE_MIN} "
                    "payments of trailing history"
                ),
                "degraded": bool(
                    volume_judgeable
                    and volume_ratio is not None
                    and volume_ratio < config.FORMING_VOLUME_COLLAPSE_RATIO
                ),
            },
        },
    }


# How extreme a degraded indicator is, and which end of that is worst. Latency
# and timeout share climb; a collapsing volume falls. Written out rather than
# inferred, so adding an indicator forces the question to be answered.
_INDICATOR_EXTREME = {
    "timeout_share": max,
    "mean_latency_ms": max,
    "volume_rate": min,
}


def _indicator_strength(indicator: dict[str, Any]) -> float:
    """The comparable magnitude of one indicator reading against its baseline."""
    for key in ("ratio", "delta"):
        if indicator.get(key) is not None:
            return float(indicator[key])
    return 0.0


def _degraded_names(reading: dict[str, Any]) -> list[str]:
    return [name for name in FORMING_INDICATORS if reading["indicators"][name]["degraded"]]


def store_dimension_values(connection: sqlite3.Connection, dimension: str) -> list[Any]:
    """Distinct values of one dimension across the whole store, ordered.

    Drawn from the whole store rather than from the window on purpose: a cohort
    that has been routed around has no rows *in* the window at all, so
    enumerating from the window would make the outage case unreachable.
    """
    if dimension not in schema.DIMENSIONS:
        raise ValueError(f"{dimension!r} is not a cohort dimension")
    rows = connection.execute(
        f"SELECT DISTINCT {dimension} AS value FROM attempt "
        f"WHERE {dimension} IS NOT NULL ORDER BY {dimension}"
    ).fetchall()
    return [row["value"] for row in rows]


def _contains_formed_traffic(
    cohort: dict[str, Any], formed_cohort: dict[str, Any] | None
) -> bool:
    """Does this candidate slice contain the traffic of an already-reported incident?

    A slice that does is degraded *because of* that incident, and warning about
    it a second time under a different name is noise, not an earlier warning.
    A slice on the same dimension with a different value - the healthy sibling
    provider - is disjoint from it and stays eligible.
    """
    if not formed_cohort:
        return False
    return any(
        dimension not in formed_cohort or formed_cohort[dimension] == value
        for dimension, value in cohort.items()
    )


def cohorts_same_episode(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    """True when one cohort is the other, or a sharpening of it.

    Localisation deepens as a drop grows. A watch on `{provider: adyen}` and a
    later incident on `{country: CO, merchant_id: merchant-b, provider: adyen}`
    are one episode, so they keep one record. Disjoint slices are not.
    """
    first = dict(left or {})
    second = dict(right or {})
    if first == second:
        return True
    if not first or not second:
        return False
    return _is_sharpening(first, second) or _is_sharpening(second, first)


def _is_sharpening(general: dict[str, Any], specific: dict[str, Any]) -> bool:
    return all(specific.get(key) == value for key, value in general.items())


def _watch_candidates(
    connection: sqlite3.Connection,
    start: int,
    end: int,
    formed_cohort: dict[str, Any] | None,
) -> list[tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any], list[str]]]:
    """Cohorts eligible to be watched, with their conversion and indicator reads.

    Reporting follows the same contrast rule as `localise`: a dimension only
    earns a place when one of its values is degraded and another is not. If
    every merchant behind a slow provider is equally slow, the merchant is not
    the story, and reporting one would be a coincidence dressed up as a
    finding. When nothing localises, the platform itself is reported, which is
    the honest answer to a slowdown that really is everywhere.
    """
    def read(cohort):
        conversion = evaluate(connection, cohort, start, end)
        reading = leading_indicators(connection, cohort, start, end)
        series = metrics.timeseries(connection, cohort, start, end)
        trajectory = trajectory_of(series)
        floors = watch_floors(conversion, trajectory)
        degraded = [] if conversion["qualifies"] else _degraded_names(reading)
        reasons = []
        if all(floors.values()):
            reasons.append("conversion_near_miss")
        if degraded:
            reasons.append("leading_indicators")
        return conversion, reading, floors, trajectory, degraded, reasons

    localised: list = []
    for dimension in schema.DIMENSIONS:
        values = store_dimension_values(connection, dimension)
        if len(values) < 2:
            continue
        siblings = []
        for value in values:
            cohort = {dimension: value}
            if _contains_formed_traffic(cohort, formed_cohort):
                continue
            siblings.append((cohort,) + read(cohort))
        if not siblings or all(entry[-1] for entry in siblings):
            continue  # no contrast: this dimension is not what separates them
        localised.extend(entry for entry in siblings if entry[-1])

    if not localised and not formed_cohort:
        entry = (None,) + read(None)
        if entry[-1]:
            localised.append(entry)
    return localised


def build_watches(
    connection: sqlite3.Connection,
    start: int,
    end: int,
    formed_cohort: dict[str, Any] | None = None,
    merchant_normals: dict[str, float] | None = None,
    identify=None,
) -> list[dict[str, Any]]:
    """C3 records in `lifecycle_state: watching` for every cohort worth watching.

    A cohort that already qualifies as an incident is not watched - it has
    formed, and the detected record is the right way to say so. `formed_cohort`
    is that incident's affected cohort when the sweep reported one, so the
    slices carrying its traffic are not re-reported here under other names.
    """
    candidates = _watch_candidates(connection, start, end, formed_cohort)

    # A conversion near-miss appears in every slice that contains the affected
    # traffic - the provider, and also the country, the bank and the merchant
    # it happens to be diluted into. Only the strongest is reported, which is
    # the same instinct `localise` applies to an incident: name the cohort the
    # deviation is concentrated in, not every slice it leaks into. Localisation
    # itself cannot do the job here, because a deviation small enough to be a
    # near-miss is by definition too small to clear its separation rule.
    keep: list = []
    near_misses = [entry for entry in candidates if "conversion_near_miss" in entry[-1]]
    near_miss_cohort = None
    if near_misses:
        chosen_near_miss = min(
            near_misses, key=lambda e: (e[1]["z"], metrics.cohort_key(e[1]["cohort"]))
        )
        keep.append(chosen_near_miss)
        near_miss_cohort = chosen_near_miss[0]

    # The same dilution happens to a leading indicator, and worse: a slow
    # provider makes its merchant, its country, its card scheme and every bank
    # behind it read slow too, so one cause arrives as eight rows. Each
    # degraded indicator therefore names the single cohort it is most extreme
    # in - the one the degradation is concentrated in rather than diluted
    # through. Different indicators may still name different cohorts, because
    # a slow provider and a routed-around one are two findings, not one.
    # A conversion near-miss already names that traffic: do not also warn on
    # an equal or broader slice of it (the provider watch, or the platform).
    # Missing a dimension is not containment - `{country: CO}` is not a
    # sharpening of `{provider: adyen}`, and suppressing it would hide an
    # independent latency watch.
    for indicator, best in _INDICATOR_EXTREME.items():
        holders = [entry for entry in candidates if indicator in entry[-2]]
        if near_miss_cohort is not None:
            holders = [
                entry
                for entry in holders
                if not _is_sharpening(entry[0] or {}, near_miss_cohort)
            ]
        if not holders:
            continue
        strongest = best(
            holders,
            key=lambda e: (
                _indicator_strength(e[2]["indicators"][indicator]),
                metrics.cohort_key(e[2]["cohort"]),
            ),
        )
        if strongest not in keep:
            keep.append(strongest)
    candidates = [entry for entry in candidates if entry in keep]

    watches: list[dict[str, Any]] = []
    for cohort, conversion, reading, floors, trajectory, degraded, reasons in candidates:
        # A clearly improved conversion plus a modest latency bump is mix
        # noise, not a forming outage. Measured: visa at z +1.57 with latency
        # 1.6x on a warm store. Extreme latency, timeout share, or volume
        # collapse still watch - that is ADR 0024's conversion-healthy path.
        z = conversion.get("z")
        if reasons == ["leading_indicators"] and z is not None and z > 1.0:
            indicators = reading["indicators"]
            latency_ratio = indicators["mean_latency_ms"].get("ratio") or 0.0
            strong = (
                indicators["timeout_share"]["degraded"]
                or indicators["volume_rate"]["degraded"]
                or latency_ratio >= 3.0
            )
            if not strong:
                continue
        watch = _watch_block(reasons, floors, trajectory, reading["indicators"], degraded)
        record = _c3_record(
            connection,
            [conversion],
            conversion,
            start,
            end,
            "watch-0001",
            store.WATCHING,
            merchant_normals,
            watch=watch,
        )
        if identify is not None:
            record["incident_id"] = identify(record)
        watches.append(record)
    watches.sort(key=lambda record: metrics.cohort_key(record["affected_cohort"]))
    return watches
