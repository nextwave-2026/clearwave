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


def _where(cohort: dict[str, Any] | None, start: int, end: int) -> tuple[str, list[Any]]:
    """Build the shared time-and-cohort predicate. Half-open window."""
    clauses = ["occurred_epoch >= ?", "occurred_epoch < ?"]
    params: list[Any] = [start, end]
    for dimension, value in sorted((cohort or {}).items()):
        if dimension not in schema.DIMENSIONS:
            raise ValueError(f"{dimension!r} is not a cohort dimension")
        clauses.append(f"{dimension} = ?")
        params.append(value)
    return " AND ".join(clauses), params


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
    return {
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
    }


def _percentile(ordered: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile over a pre-sorted list."""
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


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
        result[f"affected_{dimension}s"] = int(row["n"] or 0)
    return result


def financial_impact(
    connection: sqlite3.Connection,
    cohort: dict[str, Any] | None,
    start: int,
    end: int,
    expected_conversion: float,
) -> dict[str, Any]:
    """GMV at risk, priced on payments and never on attempts.

    Pricing attempts would inflate the loss exactly when a retry storm makes
    the number matter most.
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
        "assumptions": [
            "GMV at risk is an estimate of approved volume not captured, not a platform-revenue claim.",
            "Expected approval rate is the measured baseline for this cohort, not a target.",
            "Value is priced per payment; retries are never counted as additional payments.",
            f"Currency conversion uses the frozen table in config {config.CONFIG_VERSION}.",
        ],
    }
