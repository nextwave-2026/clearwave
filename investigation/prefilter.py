"""Deterministic hypothesis pre-filter over observable evidence only.

The failure-mode patterns are transcribed from the failure dimensions in
``docs/prd.md`` because ``docs/domain/failure-modes.md`` is not present yet.
That taxonomy file is the intended future source of truth. This module never
reads a scenario identifier, hidden truth, or anything under ``evaluator/``;
its only inputs are a C3 incident and C2 opening responses.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any


_EPSILON = 0.01

# Compatibility shim for the C3 naming mismatch; remove after the emitter is fixed.
_AFFECTED_RADIUS_KEYS = {
    "merchant_id": ("affected_merchants", "affected_merchant_ids"),
    "provider": ("affected_providers",),
    "payment_method": ("affected_payment_methods",),
    "card_network": ("affected_card_networks",),
    "country": ("affected_countries", "affected_countrys"),
    "issuing_bank": ("affected_issuing_banks",),
}


def compute_signature(
    incident: Mapping[str, Any],
    opening_evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute a stable signature from C3 facts and the opening C2 bundle."""
    metrics = _mapping(opening_evidence.get("cohort_metrics"))
    payment = _mapping(metrics.get("payment_metrics"))
    attempts = _mapping(metrics.get("attempt_metrics"))
    baseline = _mapping(metrics.get("baseline"))
    change = _mapping(incident.get("change"))

    payment_actual = _number(payment.get("approval_conversion"), change.get("actual"))
    payment_expected = _number(
        payment.get("expected_approval_conversion"),
        baseline.get("payment_approval_conversion"),
        change.get("expected"),
    )
    attempt_actual = _number(attempts.get("approval_conversion"))
    attempt_expected = _number(baseline.get("attempt_approval_conversion"))
    payment_delta = _delta(payment_actual, payment_expected)
    attempt_delta = _delta(attempt_actual, attempt_expected)
    current_gap = _delta(payment_actual, attempt_actual)
    baseline_gap = _delta(payment_expected, attempt_expected)

    operational = _mapping(opening_evidence.get("operational_metrics"))
    latency = _mapping(operational.get("latency_ms"))
    decline = _mapping(opening_evidence.get("decline_breakdown"))
    retry = _mapping(opening_evidence.get("retry_stats"))
    queue = _mapping(retry.get("queue"))
    health = _mapping(operational.get("service_health"))
    runtime = _mapping(operational.get("runtime_health"))
    compare = _mapping(opening_evidence.get("cohort_compare"))
    confounding = _mapping(opening_evidence.get("confounding_check"))
    cohort = _mapping(incident.get("affected_cohort"))

    timeout_rate = _number(operational.get("timeout_rate"))
    error_rate = _number(operational.get("error_rate"))
    p95 = _number(latency.get("p95"))
    p99 = _number(latency.get("p99"))
    timeout_shift = _shift_for(decline, "timeout")
    issuer_shift = _shift_for(decline, "issuer_decline")
    retry_factor = _number(retry.get("retry_amplification_factor"), retry.get("attempts_per_payment"))
    depth_start = _number(queue.get("depth_start"))
    depth_end = _number(queue.get("depth_end"))
    depth_peak = _number(queue.get("depth_peak"))
    runtime_status = str(runtime.get("status", "unknown"))
    service_status = str(health.get("status", "unknown"))
    narrowness = _cohort_scope(incident, cohort)
    target_isolated = _target_isolated(compare)

    return {
        "payment_conversion": {
            "actual": payment_actual,
            "expected": payment_expected,
            "delta": payment_delta,
            "direction": _direction(payment_delta),
        },
        "attempt_conversion": {
            "actual": attempt_actual,
            "expected": attempt_expected,
            "delta": attempt_delta,
            "direction": _direction(attempt_delta),
        },
        "conversion_gap": {
            "payment_minus_attempt": current_gap,
            "baseline_payment_minus_attempt": baseline_gap,
            "shift": _delta(current_gap, baseline_gap),
            "direction": _direction(_delta(current_gap, baseline_gap)),
        },
        "timeout_rate": {
            "value": timeout_rate,
            "elevated": timeout_rate is not None and timeout_rate >= 0.10,
            "shift": timeout_shift,
        },
        "latency": {
            "p50_ms": _number(latency.get("p50")),
            "p95_ms": p95,
            "p99_ms": p99,
            "elevated": (p95 is not None and p95 >= 1000)
            or (p99 is not None and p99 >= 2000),
        },
        "decline_mix_shift": {
            "timeout": timeout_shift,
            "issuer_decline": issuer_shift,
            "all": _all_shifts(decline),
            "largest": _largest_shift(decline),
        },
        "retry_amplification": {
            "factor": retry_factor,
            "elevated": retry_factor is not None and retry_factor > 1.10,
            "retried_payments": _number(retry.get("retried_payments")),
        },
        "queue_depth": {
            "start": depth_start,
            "end": depth_end,
            "peak": depth_peak,
            "change": _delta(depth_end, depth_start),
            "growing": depth_start is not None and depth_end is not None and depth_end > depth_start,
        },
        "service_health": service_status,
        "runtime_health": runtime_status,
        "error_rate": error_rate,
        "deployment": _mapping(operational.get("deployment")),
        "onset": incident.get("onset"),
        "affected_cohort": {
            "dimensions": sorted(cohort),
            "dimension_count": len(cohort),
            "scope": narrowness["scope"],
            "width": narrowness["width"],
        },
        "target_isolated": target_isolated,
        "structurally_inseparable": bool(confounding.get("structurally_inseparable", False)),
    }


def prefilter(
    incident: Mapping[str, Any],
    opening_evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return an evidence signature and ranked observable failure hypotheses."""
    signature = compute_signature(incident, opening_evidence)
    candidates: list[dict[str, Any]] = []
    for name, scorer in _PATTERNS:
        score, supporting, against = scorer(signature)
        if score > 0.20:
            candidates.append(
                {
                    "name": name,
                    "hypothesis": name,
                    "score": round(score, 4),
                    "supporting_signature": supporting,
                    "arguing_against": against,
                    "support": supporting,
                    "against": against,
                }
            )
    candidates.sort(key=lambda candidate: (-candidate["score"], candidate["name"]))
    if not candidates:
        candidates = [
            {
                "name": "unknown_observable_failure",
                "hypothesis": "unknown_observable_failure",
                "score": 0.0,
                "supporting_signature": [],
                "arguing_against": [],
                "support": [],
                "against": [],
            }
        ]
        reason = (
            "No failure-mode pattern crossed the evidence threshold; more "
            "discriminating observations are required."
        )
    else:
        reason = "Candidates are ranked from observable signature support; this is not a diagnosis."
    return {"signature": signature, "candidates": candidates, "reason": reason}


def rank_candidates(
    incident: Mapping[str, Any],
    opening_evidence: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Convenience API returning only the ranked candidate list."""
    return prefilter(incident, opening_evidence)["candidates"]


def _provider(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    timeout = _mapping(signature.get("timeout_rate"))
    latency = _mapping(signature.get("latency"))
    decline = _mapping(signature.get("decline_mix_shift"))
    if timeout.get("elevated"):
        score += 0.32
        supporting.append(f"timeout rate elevated ({timeout.get('value')})")
    if _above(timeout.get("shift"), 0.10):
        score += 0.20
        supporting.append(f"timeout share shifted up ({timeout.get('shift')})")
    if latency.get("elevated"):
        score += 0.18
        supporting.append("latency percentile is elevated")
    if signature.get("service_health") == "degraded":
        score += 0.22
        supporting.append("service health is degraded")
    if signature.get("target_isolated"):
        score += 0.10
        supporting.append("target is worse than sibling cohorts")
    if _above(decline.get("issuer_decline"), 0.10):
        score -= 0.20
        against.append("issuer-decline share is the dominant positive shift")
    return score, supporting, against


def _issuer(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    decline = _mapping(signature.get("decline_mix_shift"))
    timeout = _mapping(signature.get("timeout_rate"))
    if _above(decline.get("issuer_decline"), 0.10):
        score += 0.48
        supporting.append(f"issuer-decline share shifted up ({decline.get('issuer_decline')})")
    if signature.get("structurally_inseparable"):
        score += 0.24
        supporting.append("provider and issuer are structurally inseparable")
    if _below(timeout.get("value"), 0.10) and signature.get("service_health") == "healthy":
        score += 0.18
        supporting.append("service health is healthy and timeouts are not elevated")
    if timeout.get("elevated"):
        score -= 0.22
        against.append("timeout rate is elevated")
    if signature.get("service_health") == "degraded":
        score -= 0.15
        against.append("first-party service health is degraded")
    return score, supporting, against


def _scope_pattern(
    signature: Mapping[str, Any], dimension: str, label: str
) -> tuple[float, list[str], list[str]]:
    cohort = _mapping(signature.get("affected_cohort"))
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    if dimension in cohort.get("dimensions", []):
        score += 0.42
        supporting.append(f"affected cohort includes {dimension}")
    if cohort.get("scope") == "narrow":
        score += 0.20
        supporting.append("affected scope is narrow")
    if signature.get("target_isolated"):
        score += 0.20
        supporting.append("target is isolated from siblings")
    if cohort.get("scope") == "broad":
        score -= 0.20
        against.append("affected scope is broad")
    return score, supporting, against


def _retry(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    retry = _mapping(signature.get("retry_amplification"))
    queue = _mapping(signature.get("queue_depth"))
    gap = _mapping(signature.get("conversion_gap"))
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    if retry.get("elevated"):
        score += 0.50
        supporting.append(f"retry amplification is {retry.get('factor')}")
    if queue.get("growing"):
        score += 0.30
        supporting.append(f"queue depth grew by {queue.get('change')}")
    if _above(gap.get("shift"), 0.05):
        score += 0.15
        supporting.append("attempt/payment conversion gap widened")
    if not retry.get("elevated"):
        against.append("retry amplification is not elevated")
    return score, supporting, against


def _application(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    error_rate = signature.get("error_rate")
    if error_rate is not None and error_rate >= 0.05:
        score += 0.50
        supporting.append(f"error rate is elevated ({error_rate})")
    if signature.get("runtime_health") in {"degraded", "unhealthy"}:
        score += 0.35
        supporting.append(f"runtime health is {signature.get('runtime_health')}")
    if _mapping(signature.get("deployment")).get("deployment_id"):
        score += 0.12
        supporting.append("a deployment identity is present for correlation")
    if signature.get("service_health") == "degraded":
        score -= 0.12
        against.append("service health points to a lower-level service issue")
    if not supporting:
        against.append("no elevated application or runtime signal")
    return score, supporting, against


def _infrastructure(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    queue = _mapping(signature.get("queue_depth"))
    latency = _mapping(signature.get("latency"))
    if signature.get("runtime_health") in {"degraded", "unhealthy"}:
        score += 0.48
        supporting.append(f"runtime health is {signature.get('runtime_health')}")
    if queue.get("growing"):
        score += 0.25
        supporting.append("queue depth is growing")
    if latency.get("elevated"):
        score += 0.25
        supporting.append("latency is elevated")
    if signature.get("runtime_health") == "healthy":
        against.append("runtime health is healthy")
    return score, supporting, against


def _deployment(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    deployment = _mapping(signature.get("deployment"))
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    deployed_at = _parse_time(deployment.get("deployed_at"))
    onset = _parse_time(signature.get("onset"))
    if deployment.get("deployment_id"):
        score += 0.20
        supporting.append(f"deployment {deployment.get('deployment_id')} is available for correlation")
    if deployed_at is not None and (onset is None or deployed_at <= onset):
        score += 0.45
        supporting.append("deployment preceded the observed degradation")
    if not deployment.get("deployment_id"):
        against.append("no deployment identity was observed")
    return score, supporting, against


def _queue(signature: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    queue = _mapping(signature.get("queue_depth"))
    retry = _mapping(signature.get("retry_amplification"))
    score = 0.0
    supporting: list[str] = []
    against: list[str] = []
    if queue.get("growing"):
        score += 0.60
        supporting.append(f"queue grew by {queue.get('change')}")
    if retry.get("elevated"):
        score += 0.20
        supporting.append("retries are amplifying queued work")
    if queue.get("peak") is not None:
        score += 0.10
        supporting.append(f"queue peak reached {queue.get('peak')}")
    if not queue.get("growing"):
        against.append("queue depth is not growing")
    return score, supporting, against


_PATTERNS = (
    ("provider_degradation", _provider),
    ("issuer_over_decline", _issuer),
    ("payment_method_failure", lambda s: _scope_pattern(s, "payment_method", "payment method")),
    ("country_specific_failure", lambda s: _scope_pattern(s, "country", "country")),
    ("routing_problem", lambda s: _scope_pattern(s, "provider", "routing")),
    ("retry_amplification", _retry),
    ("application_failure", _application),
    ("infrastructure_failure", _infrastructure),
    ("deployment_configuration_problem", _deployment),
    ("queue_buildup", _queue),
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else round(left - right, 10)


def _direction(value: float | None) -> str:
    if value is None or abs(value) < _EPSILON:
        return "unchanged"
    return "up" if value > 0 else "down"


def _above(value: Any, threshold: float) -> bool:
    return isinstance(value, (int, float)) and value > threshold


def _below(value: Any, threshold: float) -> bool:
    return isinstance(value, (int, float)) and value < threshold


def _shift_for(decline: Mapping[str, Any], reason: str) -> float | None:
    for item in decline.get("reasons", ()):
        if isinstance(item, Mapping) and item.get("reason") == reason:
            return _number(item.get("shift"))
    return None


def _all_shifts(decline: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(item.get("reason")): float(item["shift"])
        for item in decline.get("reasons", ())
        if isinstance(item, Mapping)
        and item.get("reason") is not None
        and isinstance(item.get("shift"), (int, float))
    }


def _largest_shift(decline: Mapping[str, Any]) -> dict[str, Any] | None:
    items = [
        item
        for item in decline.get("reasons", ())
        if isinstance(item, Mapping) and isinstance(item.get("shift"), (int, float))
    ]
    if not items:
        return None
    item = max(items, key=lambda value: (abs(float(value["shift"])), str(value.get("reason", ""))))
    return {"reason": item.get("reason"), "shift": float(item["shift"])}


def _cohort_scope(incident: Mapping[str, Any], cohort: Mapping[str, Any]) -> dict[str, Any]:
    radius = _mapping(incident.get("blast_radius"))
    counts = [_affected_count(radius, dimension) for dimension in _AFFECTED_RADIUS_KEYS]
    broad_dimensions = sum(1 for count in counts if isinstance(count, (int, float)) and count > 1)
    width = broad_dimensions or len(cohort)
    return {"scope": "broad" if broad_dimensions or not cohort else "narrow", "width": width}


def _affected_count(radius: Mapping[str, Any], dimension: str) -> Any:
    for key in _AFFECTED_RADIUS_KEYS[dimension]:
        if key in radius:
            return radius[key]
    return None


def _target_isolated(compare: Mapping[str, Any]) -> bool:
    target_metrics = _mapping(_mapping(compare.get("target")).get("payment_metrics"))
    target = _number(target_metrics.get("approval_conversion"))
    siblings = []
    for sibling in compare.get("siblings", ()):
        if isinstance(sibling, Mapping):
            value = _number(_mapping(sibling.get("payment_metrics")).get("approval_conversion"))
            if value is not None:
                siblings.append(value)
    return target is not None and bool(siblings) and target < min(siblings) - 0.05


evidence_signature = compute_signature
prefilter_hypotheses = prefilter


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
