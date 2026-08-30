"""Deterministic measurement over the stored canonical events.

Everything the investigation agent is ever allowed to call as a fact is
computed here, from integers, with no model in the path.

The one semantic that needs stating out loud: within a cohort filter, a
payment counts as approved only if it was approved *through that cohort*. So a
payment that failed on provider P2 and then succeeded on P3 is a failure in the
P2 slice and a success in the P3 slice. That is what makes the gap between
payment-level and attempt-level conversion meaningful - it is the difference
between "the fallback absorbed it" and "money left".
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import config, schema


# Columns a measurement filter may be built on. The six cohort dimensions are
# the published slice space; the two operational columns are here because
# `operational_metrics` can be asked about a service rather than a cohort. The
# published C2 surface still validates a *cohort* against schema.DIMENSIONS
# alone, so this list widens what can be measured, never what a cohort means.
FILTERABLE = schema.DIMENSIONS + ("service_id", "deployment_id")


def _where(cohort: dict[str, Any] | None, start: int, end: int) -> tuple[str, list[Any]]:
    """Build the shared time-and-cohort predicate. Half-open window."""
    clauses = ["occurred_epoch >= ?", "occurred_epoch < ?"]
    params: list[Any] = [start, end]
    for dimension, value in sorted((cohort or {}).items()):
        if dimension not in FILTERABLE:
            raise ValueError(f"{dimension!r} is not a filterable dimension")
        clauses.append(f"{dimension} = ?")
        params.append(value)
    return " AND ".join(clauses), params


def dimension_values(
    connection: sqlite3.Connection,
    dimension: str,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> list[Any]:
    """Distinct observed values of one dimension inside a cohort and window.

    Ordered, so a sibling comparison built from it is the same on every run.
    """
    if dimension not in schema.DIMENSIONS:
        raise ValueError(f"{dimension!r} is not a cohort dimension")
    where, params = _where(cohort, start, end)
    rows = connection.execute(
        f"SELECT DISTINCT {dimension} AS value FROM attempt "
        f"WHERE {where} AND {dimension} IS NOT NULL ORDER BY {dimension}",
        params,
    ).fetchall()
    return [row["value"] for row in rows]


def cohort_key(cohort: dict[str, Any] | None) -> str:
    """Stable, human-readable identity for a cohort.

    Readable on purpose: it shows up in logs and on screen, and a judge should
    be able to read it without a decoder.
    """
    if not cohort:
        return "*"
    return "|".join(f"{k}={cohort[k]}" for k in sorted(cohort))


def payment_metrics(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Payment-level conversion and value, counting each payment once."""
    where, params = _where(cohort, start, end)
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS payments,
               SUM(approved) AS approved,
               SUM(value) AS attempted_value,
               SUM(CASE WHEN approved = 1 THEN value ELSE 0 END) AS approved_value
        FROM (
            SELECT payment_id,
                   MAX(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                   MAX(amount_usd) AS value
            FROM attempt WHERE {where}
            GROUP BY payment_id
        )
        """,
        params,
    ).fetchone()
    payments = int(row["payments"] or 0)
    approved = int(row["approved"] or 0)
    return {
        "attempted_payments": payments,
        "approved_payments": approved,
        "approval_conversion": (approved / payments) if payments else None,
        "attempted_value_usd": round(float(row["attempted_value"] or 0.0), 2),
        "approved_value_usd": round(float(row["approved_value"] or 0.0), 2),
    }


def attempt_metrics(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Attempt-level conversion: what the provider surface is actually doing."""
    where, params = _where(cohort, start, end)
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS attempts,
               SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved
        FROM attempt WHERE {where}
        """,
        params,
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    approved = int(row["approved"] or 0)
    return {
        "attempts": attempts,
        "approved_attempts": approved,
        "failed_attempts": attempts - approved,
        "approval_conversion": (approved / attempts) if attempts else None,
    }


def decline_mix(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Distribution of normalised decline reasons over failed attempts.

    The denominator is failed attempts, not all attempts, and it is reported
    alongside so nobody has to guess which one produced a share.
    """
    where, params = _where(cohort, start, end)
    placeholders = ", ".join("?" for _ in schema.FAILED_STATUSES)
    rows = connection.execute(
        f"""
        SELECT normalized_decline_reason AS reason, COUNT(*) AS count
        FROM attempt
        WHERE {where} AND status IN ({placeholders})
        GROUP BY normalized_decline_reason
        ORDER BY count DESC, reason ASC
        """,
        params + list(schema.FAILED_STATUSES),
    ).fetchall()
    total = sum(int(row["count"]) for row in rows)
    return [
        {
            "reason": row["reason"],
            "count": int(row["count"]),
            "share": (int(row["count"]) / total) if total else 0.0,
        }
        for row in rows
    ]


def retry_profile(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Retry depth and amplification, without treating a retry as a new payment.

    Amplification is the signal a retry storm leaves behind: attempts climb
    while payments do not.
    """
    where, params = _where(cohort, start, end)
    rows = connection.execute(
        f"""
        SELECT payment_id, COUNT(*) AS attempts
        FROM attempt WHERE {where}
        GROUP BY payment_id
        """,
        params,
    ).fetchall()
    payments = len(rows)
    attempts = sum(int(row["attempts"]) for row in rows)
    distribution: dict[str, int] = {}
    for row in rows:
        depth = int(row["attempts"]) - 1
        distribution[str(depth)] = distribution.get(str(depth), 0) + 1
    return {
        "payments": payments,
        "attempts": attempts,
        "retried_payments": sum(1 for row in rows if int(row["attempts"]) > 1),
        "retry_depth": {
            "max": max((int(row["attempts"]) - 1 for row in rows), default=0),
            "distribution": dict(sorted(distribution.items(), key=lambda item: int(item[0]))),
        },
        "attempts_per_payment": (attempts / payments) if payments else None,
        "retry_amplification_factor": (attempts / payments) if payments else None,
    }


def operational_metrics(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Latency percentiles, error and timeout rates, queue and deployment.

    Percentiles come from the stored samples rather than an estimator, because
    at our volume an exact percentile is cheap and an approximate one is a
    thing we would have to defend.
    """
    where, params = _where(cohort, start, end)
    latencies = [
        float(row["latency_ms"])
        for row in connection.execute(
            f"SELECT latency_ms FROM attempt WHERE {where} AND latency_ms IS NOT NULL "
            "ORDER BY latency_ms",
            params,
        ).fetchall()
    ]
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS attempts,
               SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
               SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) AS timeouts,
               MAX(queue_depth) AS queue_peak,
               MAX(queue_delay_ms) AS queue_delay_max
        FROM attempt WHERE {where}
        """,
        params,
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    deployments = [
        deployment["deployment_id"]
        for deployment in connection.execute(
            f"SELECT DISTINCT deployment_id FROM attempt WHERE {where} "
            "AND deployment_id IS NOT NULL ORDER BY deployment_id",
            params,
        ).fetchall()
    ]
    services = [
        service["service_id"]
        for service in connection.execute(
            f"SELECT DISTINCT service_id FROM attempt WHERE {where} "
            "AND service_id IS NOT NULL ORDER BY service_id",
            params,
        ).fetchall()
    ]
    return {
        "attempts": attempts,
        "errors": int(row["errors"] or 0),
        "timeouts": int(row["timeouts"] or 0),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "error_rate": (int(row["errors"] or 0) / attempts) if attempts else None,
        "timeout_rate": (int(row["timeouts"] or 0) / attempts) if attempts else None,
        "queue": {
            "depth_peak": row["queue_peak"],
            "delay_max_ms": row["queue_delay_max"],
        },
        "deployments": deployments,
        "services": services,
    }


def runtime_health(
    connection: sqlite3.Connection,
    services: list[str] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Service-level runtime health, measured from W1's `ops.telemetry` samples.

    This is the one signal the attempt stream cannot carry: an attempt tells us
    what a provider did, never whether our own router was healthy. Until a
    sample arrives the honest answer stays `unobserved` with its reason, which
    is exactly what the file-based demo path reports.

    Bucketed on `sample_ts` like everything else, so a replay reproduces it.
    """
    where = ["sample_epoch >= ?", "sample_epoch < ?"]
    params: list[Any] = [start, end]
    if services:
        where.append(f"service_id IN ({', '.join('?' for _ in services)})")
        params.extend(services)
    clause = " AND ".join(where)

    row = connection.execute(
        f"""
        SELECT COUNT(*) AS samples,
               SUM(CASE WHEN healthy = 0 THEN 1 ELSE 0 END) AS unhealthy,
               MAX(queue_depth) AS queue_depth_peak,
               MAX(queue_delay_p95_ms) AS queue_delay_p95_max,
               MAX(cpu_pct) AS cpu_pct_peak,
               MAX(error_rate) AS error_rate_peak,
               MAX(restarts_total) AS restarts_total
        FROM telemetry_sample WHERE {clause}
        """,
        params,
    ).fetchone()
    samples = int(row["samples"] or 0)
    if samples == 0:
        return {
            "status": "unobserved",
            "reason": (
                "no operational telemetry sample has been observed for this target in this "
                "window, so W2 reports none rather than inferring one from attempts"
            ),
            "samples": 0,
        }

    unhealthy = int(row["unhealthy"] or 0)
    observed = [
        service["service_id"]
        for service in connection.execute(
            f"SELECT DISTINCT service_id FROM telemetry_sample WHERE {clause} ORDER BY service_id",
            params,
        ).fetchall()
    ]
    deployments = [
        deployment["deployment_id"]
        for deployment in connection.execute(
            f"SELECT DISTINCT deployment_id FROM telemetry_sample WHERE {clause} "
            "AND deployment_id IS NOT NULL ORDER BY deployment_id",
            params,
        ).fetchall()
    ]
    return {
        "status": "degraded" if unhealthy else "healthy",
        "criterion": (
            "reported by the service itself on ops.telemetry: degraded when any sample in the "
            "window reports healthy=false"
        ),
        "samples": samples,
        "unhealthy_samples": unhealthy,
        "queue_depth_peak": row["queue_depth_peak"],
        "queue_delay_p95_max_ms": row["queue_delay_p95_max"],
        "cpu_pct_peak": row["cpu_pct_peak"],
        "error_rate_peak": row["error_rate_peak"],
        "restarts_total": row["restarts_total"],
        "observed_services": observed,
        # A change of value between samples is the deployment marker W1's schema
        # names. More than one id in a window is that change, observed.
        "observed_deployment_ids": deployments,
    }


def _percentile(ordered: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile over a pre-sorted list."""
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def queue_profile(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Queue depth at the window edges and its peak, plus delay percentiles.

    Edges are taken from the first and last *observed* sample in event-time
    order, tie-broken on ``event_id``, so a replay in another arrival order
    reads the same two rows.
    """
    where, params = _where(cohort, start, end)
    edges = connection.execute(
        f"SELECT queue_depth FROM attempt WHERE {where} AND queue_depth IS NOT NULL "
        "ORDER BY occurred_epoch, event_id",
        params,
    ).fetchall()
    row = connection.execute(
        f"SELECT MAX(queue_depth) AS peak FROM attempt WHERE {where}", params
    ).fetchone()
    delays = [
        float(sample["queue_delay_ms"])
        for sample in connection.execute(
            f"SELECT queue_delay_ms FROM attempt WHERE {where} AND queue_delay_ms IS NOT NULL "
            "ORDER BY queue_delay_ms",
            params,
        ).fetchall()
    ]
    return {
        "depth_start": edges[0]["queue_depth"] if edges else None,
        "depth_end": edges[-1]["queue_depth"] if edges else None,
        "depth_peak": row["peak"],
        "delay_p50_ms": _percentile(delays, 0.50),
        "delay_p95_ms": _percentile(delays, 0.95),
    }


def attempt_timeseries(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
    bucket_seconds: int = config.BUCKET_SECONDS,
) -> list[dict[str, Any]]:
    """Attempt-level counters per bucket, oldest first.

    The attempt-level companion to ``timeseries``. An attempt falls in the
    bucket of its own event time, while a payment falls in the bucket of its
    first attempt, which is what keeps a retry from moving a payment forward
    in time.
    """
    where, params = _where(cohort, start, end)
    rows = connection.execute(
        f"""
        SELECT occurred_epoch - (occurred_epoch % ?) AS bucket_ts,
               COUNT(*) AS attempts,
               SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved
        FROM attempt WHERE {where}
        GROUP BY bucket_ts ORDER BY bucket_ts
        """,
        [bucket_seconds] + params,
    ).fetchall()
    series = []
    for row in rows:
        attempts = int(row["attempts"] or 0)
        approved = int(row["approved"] or 0)
        series.append(
            {
                "bucket_start_epoch": int(row["bucket_ts"]),
                "attempts": attempts,
                "approved_attempts": approved,
                "failed_attempts": attempts - approved,
                "approval_conversion": (approved / attempts) if attempts else None,
            }
        )
    return series


def timeseries(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
    bucket_seconds: int = config.BUCKET_SECONDS,
) -> list[dict[str, Any]]:
    """Payment conversion and volume per bucket, oldest first.

    This is the query the published C2 surface is missing. An incident onset,
    a severity trajectory and the demo question "since when" all need a metric
    over time, and none of the ten tools returns one.
    """
    where, params = _where(cohort, start, end)
    rows = connection.execute(
        f"""
        SELECT bucket_ts,
               COUNT(*) AS payments,
               SUM(approved) AS approved,
               SUM(value) AS attempted_value
        FROM (
            SELECT payment_id,
                   MIN(occurred_epoch - (occurred_epoch % ?)) AS bucket_ts,
                   MAX(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                   MAX(amount_usd) AS value
            FROM attempt WHERE {where}
            GROUP BY payment_id
        )
        GROUP BY bucket_ts ORDER BY bucket_ts
        """,
        [bucket_seconds] + params,
    ).fetchall()
    series = []
    for row in rows:
        payments = int(row["payments"] or 0)
        approved = int(row["approved"] or 0)
        series.append(
            {
                "bucket_start_epoch": int(row["bucket_ts"]),
                "attempted_payments": payments,
                "approved_payments": approved,
                "approval_conversion": (approved / payments) if payments else None,
                "attempted_value_usd": round(float(row["attempted_value"] or 0.0), 2),
            }
        )
    return series


def confounding(
    connection: sqlite3.Connection,
    dimension_a: str,
    dimension_b: str,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Cross-tabulate two dimensions to test whether evidence can separate them.

    This is a data property, not a judgement. If every value of A appears with
    exactly one value of B and vice versa, no amount of reasoning over this
    window can tell an A cause from a B cause - and saying so is the required
    behaviour, not a failure.
    """
    for dimension in (dimension_a, dimension_b):
        if dimension not in schema.DIMENSIONS:
            raise ValueError(f"{dimension!r} is not a cohort dimension")
    where, params = _where(cohort, start, end)
    rows = connection.execute(
        f"""
        SELECT {dimension_a} AS a, {dimension_b} AS b,
               COUNT(DISTINCT payment_id) AS payments, COUNT(*) AS attempts
        FROM attempt WHERE {where}
        GROUP BY a, b ORDER BY a, b
        """,
        params,
    ).fetchall()

    a_to_b: dict[Any, set] = {}
    b_to_a: dict[Any, set] = {}
    for row in rows:
        a_to_b.setdefault(row["a"], set()).add(row["b"])
        b_to_a.setdefault(row["b"], set()).add(row["a"])
    inseparable = bool(rows) and all(len(v) == 1 for v in a_to_b.values()) and all(
        len(v) == 1 for v in b_to_a.values()
    )
    return {
        "dimension_a": dimension_a,
        "dimension_b": dimension_b,
        "structurally_inseparable": inseparable,
        "criterion": (
            "every observed value of each dimension co-occurs with exactly one "
            "value of the other within this window"
        ),
        "cross_tabulation": {
            "dimensions": [dimension_a, dimension_b],
            "rows": [
                {
                    dimension_a: row["a"],
                    dimension_b: row["b"],
                    "payments": int(row["payments"]),
                    "attempts": int(row["attempts"]),
                }
                for row in rows
            ],
        },
    }


# The C3 field name for each dimension's distinct count. Written out rather
# than built as f"affected_{dimension}s", because that rule produced
# "affected_countrys" and "affected_merchant_ids" where
# docs/contracts/incident.md publishes "affected_countries" and
# "affected_merchants" - a naming drift no test could see, because the same
# expression generated both the emitter and anything that guessed at it.
BLAST_RADIUS_FIELDS = {
    "merchant_id": "affected_merchants",
    "provider": "affected_providers",
    "payment_method": "affected_payment_methods",
    "card_network": "affected_card_networks",
    "country": "affected_countries",
    "issuing_bank": "affected_issuing_banks",
}


def blast_radius(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """How wide the affected slice is, in payments and in distinct dimensions."""
    where, params = _where(cohort, start, end)
    result: dict[str, Any] = {
        "attempted_payments": payment_metrics(connection, cohort, start, end)["attempted_payments"]
    }
    for dimension in schema.DIMENSIONS:
        row = connection.execute(
            f"SELECT COUNT(DISTINCT {dimension}) AS n FROM attempt WHERE {where}", params
        ).fetchone()
        result[BLAST_RADIUS_FIELDS[dimension]] = int(row["n"] or 0)
    return result


def financial_impact(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
    expected_conversion: float,
    typical_hourly_value: float | None = None,
) -> dict[str, Any]:
    """GMV at risk, priced on payments and never on attempts.

    Pricing attempts would inflate the loss exactly when a retry storm makes
    the number matter most.

    When `typical_hourly_value` is supplied, a second and clearly separate
    figure is published: `projected_loss_per_hour`, what this deviation would
    cost over a full hour if it continued at the rate now measured. It is
    labelled projected because it is not realised money - it is the shortfall
    applied to the cohort's own typical hourly volume rather than to the few
    minutes actually observed, which is the only honest way to state the cost
    of a deviation that has been running for four minutes. It is deliberately
    a separate key from `loss_per_hour`: nothing that ranks severity may read
    it, or a warning would page a phone on money nobody has lost yet.
    """
    payments = payment_metrics(connection, cohort, start, end)
    attempted_value = payments["attempted_value_usd"]
    expected_value = attempted_value * expected_conversion
    gmv_at_risk = max(0.0, expected_value - payments["approved_value_usd"])
    window_hours = max((end - start) / 3600.0, 1e-9)
    expected_payments = payments["attempted_payments"] * expected_conversion
    return {
        "attempted_value": {"amount": round(attempted_value, 2), "currency": config.REPORTING_CURRENCY},
        "expected_approval_rate": round(expected_conversion, 6),
        "actual_approval_rate": (
            round(payments["approval_conversion"], 6)
            if payments["approval_conversion"] is not None
            else None
        ),
        "estimated_lost_approved_volume": {
            "payments": max(0, int(round(expected_payments - payments["approved_payments"]))),
            "amount": round(gmv_at_risk, 2),
            "currency": config.REPORTING_CURRENCY,
        },
        "gmv_at_risk": {"amount": round(gmv_at_risk, 2), "currency": config.REPORTING_CURRENCY},
        "loss_per_hour": {
            "amount": round(gmv_at_risk / window_hours, 2),
            "currency": config.REPORTING_CURRENCY,
        },
        **(
            {}
            if typical_hourly_value is None
            else {
                "projected_loss_per_hour": {
                    "amount": round(
                        max(
                            0.0,
                            expected_conversion - (payments["approval_conversion"] or 0.0),
                        )
                        * typical_hourly_value,
                        2,
                    ),
                    "currency": config.REPORTING_CURRENCY,
                    "basis": (
                        "the measured conversion shortfall applied to this cohort's typical "
                        f"hourly attempted value of {round(typical_hourly_value, 2)} "
                        f"{config.REPORTING_CURRENCY}, taken from the trailing baseline window. "
                        "It is what an hour at the rate now measured would cost, not money "
                        "already lost, and it never ranks severity."
                    ),
                }
            }
        ),
        "assumptions": [
            "GMV at risk is an estimate of approved volume not captured, not a platform-revenue claim.",
            "Expected approval rate is the measured baseline for this cohort, not a target.",
            "Value is priced per payment; retries are never counted as additional payments.",
            f"Currency conversion uses the frozen table in config {config.CONFIG_VERSION}.",
        ],
    }


def attempt_pressure(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Timeout share and mean latency: the two signals that move before conversion.

    A timeout is recorded two ways in the canonical model - as a `timeout`
    status and as the closed-vocabulary decline reason `timeout` - because a
    provider that answers slowly and one that never answers are the same story
    told by different native codes. Counting either is what makes the share
    comparable across providers, which is the whole point of the vocabulary.

    Latency is reported as a mean rather than a percentile, and that is the one
    non-obvious choice here. `operational_metrics` publishes p50/p95/p99, and a
    p95 is the wrong statistic to *baseline* against: the trailing window's own
    tail is exactly where a forming degradation already sits, so a few degraded
    minutes at the end of an hour move the baseline p95 all the way up to the
    degraded value and the comparison silently cancels itself out. A mean moves
    in proportion to how much of the window is degraded, which is the same
    property that makes a mean conversion rate a usable baseline.

    Attempts, not payments: a retry that times out is another timeout, and a
    share that hid retries would flatten exactly the amplification a degrading
    provider produces.
    """
    where, params = _where(cohort, start, end)
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS attempts,
               SUM(CASE WHEN status = 'timeout' OR normalized_decline_reason = 'timeout'
                        THEN 1 ELSE 0 END) AS timeouts,
               AVG(latency_ms) AS mean_latency_ms
        FROM attempt WHERE {where}
        """,
        params,
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    timeouts = int(row["timeouts"] or 0)
    return {
        "attempts": attempts,
        "timeouts": timeouts,
        "timeout_share": (timeouts / attempts) if attempts else None,
        "mean_latency_ms": (
            round(float(row["mean_latency_ms"]), 3) if row["mean_latency_ms"] is not None else None
        ),
    }


def typical_hourly_attempted_value(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    trailing_buckets: int = config.BASELINE_TRAILING_BUCKETS,
) -> float | None:
    """This cohort's ordinary attempted value per hour, from the trailing window.

    The same window `baseline_conversion` reads, so the projected figure and
    the expectation it is applied to come from one span of history rather than
    two. Returns None when the window holds nothing to average.
    """
    trailing_start = start - trailing_buckets * config.BUCKET_SECONDS
    if trailing_start >= start:
        return None
    trailing = payment_metrics(connection, cohort, trailing_start, start)
    if trailing["attempted_payments"] == 0:
        return None
    hours = (start - trailing_start) / 3600.0
    return trailing["attempted_value_usd"] / hours
