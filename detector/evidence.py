"""The C2 evidence-query tools, answered from the SQLite store.

`stubs/evidence/*.py` are thin argv-free entry points; every answer is built
here, so the tools can be tested as functions rather than as subprocesses and
so a number a tool reports is produced by the same code the detector uses.

Three rules hold across every tool.

* **The wire shape is fixed.** `docs/contracts/evidence-tools.md` is the single
  definition and two other workstreams already build against it. Field names,
  the error envelope and the `query_id` algorithm are not ours to change.
* **An empty store answers honestly.** Zero counters, `null` where a rate is
  undefined, an empty list. Never a crash, never a borrowed fixture number.
  Silence about something we did not observe is a real answer.
* **The answer is a function of the events, not of the clock.** `as_of` is the
  measurement watermark rather than wall-clock now, so replaying the same
  events in any order reproduces the response byte for byte.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import config, detect, metrics, schema, store

# The tools this module answers. `external_status` is deliberately absent: it
# corroborates from a third-party source rather than measuring our own events,
# its ownership sits with W3, and it stays on its published fixture.
TOOLS = (
    "cohort_metrics",
    "cohort_compare",
    "drilldown",
    "decline_breakdown",
    "retry_stats",
    "operational_metrics",
    "confounding_check",
    "incident_history",
    "financial_impact",
    "metric_series",
    "ingest_health",
)

# `ingest_health` groups dead letters by reason. The reason is an exception
# string, so its cardinality is bounded by the failure modes rather than by the
# traffic, but a pathological store should not be able to return an unbounded
# list. The count of distinct reasons is always reported in full.
DEAD_LETTER_REASON_LIMIT = 10

# The event-time column of each stored record kind, for `ingest_health`.
# `store.KINDS` names the tables; only the attempt table's column feeds
# `window_bounds`, and therefore the watermark, because the canonical event is
# the attempt. Telemetry and closed-payment rows are stored alongside it and
# would otherwise be invisible on a store that holds them and nothing else.
KIND_TIME_COLUMNS = {
    "attempt": "occurred_epoch",
    "telemetry": "sample_epoch",
    "closed": "closed_epoch",
}

# Closed metric vocabulary for `metric_series`. A caller asking for anything
# else gets a refusal naming the set, never a silently substituted default.
SERIES_METRICS = (
    "payment_approval_conversion",
    "attempt_approval_conversion",
    "attempted_payments",
    "approved_payments",
    "attempts",
    "failed_attempts",
    "attempted_value_usd",
    "retry_amplification_factor",
)

# Level names as the contract writes them ("merchant"), mapped to the canonical
# dimension ("merchant_id"). Both spellings are accepted on input.
LEVEL_ALIASES = {
    (dimension[:-3] if dimension.endswith("_id") else dimension): dimension
    for dimension in schema.DIMENSIONS
}
ROOT_LEVEL = "all_traffic"


class EvidenceError(Exception):
    """A refusal that belongs in the published error envelope, with its code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def answer(tool: str, request: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    """Answer one tool call. Returns the response body, including ``as_of``."""
    handler = _HANDLERS.get(tool)
    if handler is None:
        raise EvidenceError("unknown_tool", f"{tool!r} is not a measured evidence tool")
    try:
        return handler(connection, request)
    except ValueError as exc:  # a rejected dimension from the measurement layer
        raise EvidenceError("invalid_input", str(exc)) from exc


def watermark(connection: sqlite3.Connection) -> int:
    """The event time up to which measurement is complete.

    The latest observed event, less the lateness grace, floored to a bucket.
    An empty store has observed nothing, so its watermark is the epoch.
    """
    bounds = store.window_bounds(connection)
    if bounds is None:
        return 0
    return schema.bucket_of(max(0, bounds[1] - config.LATENESS_GRACE_SECONDS))


def _as_of(connection: sqlite3.Connection, window_end: int | None = None) -> str:
    """``as_of`` is the watermark, clamped to the end of the asked-about window."""
    mark = watermark(connection)
    return schema.iso_utc(mark if window_end is None else min(mark, window_end))


def _rate(value: float | None) -> float | None:
    """Round a rate to ten places so the same events give the same JSON."""
    return None if value is None else round(float(value), 10)


def _money(amount: float) -> dict[str, Any]:
    return {"amount": round(float(amount), 2), "currency": config.REPORTING_CURRENCY}


def _object(request: dict[str, Any], key: str, required: bool = False) -> dict[str, Any]:
    value = request.get(key)
    if value is None:
        if required:
            raise EvidenceError("invalid_input", f"{key} is required and must be an object")
        return {}
    if not isinstance(value, dict):
        raise EvidenceError("invalid_input", f"{key} must be an object")
    return dict(value)


def _cohort(request: dict[str, Any], key: str = "cohort", required: bool = False) -> dict[str, Any]:
    """Read a cohort filter and hold it to the published dimension set."""
    cohort = _object(request, key, required)
    for dimension in cohort:
        if dimension not in schema.DIMENSIONS:
            raise EvidenceError(
                "invalid_input",
                f"{dimension!r} is not a cohort dimension; "
                f"supported dimensions are {', '.join(schema.DIMENSIONS)}",
            )
    return cohort


def _window(request: dict[str, Any], key: str = "window", required: bool = True):
    """Parse an inclusive-start, exclusive-end UTC interval into epochs."""
    window = request.get(key)
    if window is None:
        if required:
            raise EvidenceError("invalid_input", f"{key} is required with start and end timestamps")
        return None
    if not isinstance(window, dict):
        raise EvidenceError("invalid_input", f"{key} must be an object with start and end")
    try:
        start = schema.parse_timestamp(window.get("start"))
        end = schema.parse_timestamp(window.get("end"))
    except schema.InvalidEvent as exc:
        raise EvidenceError("invalid_input", f"{key}: {exc}") from exc
    start_epoch, end_epoch = int(start.timestamp()), int(end.timestamp())
    if end_epoch < start_epoch:
        raise EvidenceError("invalid_input", f"{key}.end must not precede {key}.start")
    return start_epoch, end_epoch


def _echo_window(request: dict[str, Any], start: int, end: int, key: str = "window") -> dict[str, Any]:
    """Echo the caller's own window when it gave one, else the resolved one."""
    given = request.get(key)
    if isinstance(given, dict) and given.get("start") and given.get("end"):
        return dict(given)
    return {"start": schema.iso_utc(start), "end": schema.iso_utc(end)}


def _payment_block(measured: dict[str, Any], expected: float | None = None) -> dict[str, Any]:
    block = {
        "attempted_payments": measured["attempted_payments"],
        "approved_payments": measured["approved_payments"],
        "approval_conversion": _rate(measured["approval_conversion"]),
    }
    if expected is not None:
        block["expected_approval_conversion"] = _rate(expected)
        block["expected_approved_payments"] = int(
            round(expected * measured["attempted_payments"])
        )
    return block


def _attempt_block(measured: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempts": measured["attempts"],
        "approved_attempts": measured["approved_attempts"],
        "failed_attempts": measured["failed_attempts"],
        "approval_conversion": _rate(measured["approval_conversion"]),
    }


def _volume_block(measured: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempted": _money(measured["attempted_value_usd"]),
        "approved": _money(measured["approved_value_usd"]),
    }


def _slice(
    connection: sqlite3.Connection,
    cohort: dict[str, Any],
    start: int,
    end: int,
    label: str,
) -> dict[str, Any]:
    """One labelled cohort measured the same way everywhere it appears."""
    payments = metrics.payment_metrics(connection, cohort or None, start, end)
    attempts = metrics.attempt_metrics(connection, cohort or None, start, end)
    return {
        "label": label,
        "cohort": dict(cohort),
        "payment_metrics": _payment_block(payments),
        "attempt_metrics": _attempt_block(attempts),
        "volume": _volume_block(payments),
    }


def _cohort_label(cohort: dict[str, Any]) -> str:
    return metrics.cohort_key(cohort) if cohort else "all traffic"


# --------------------------------------------------------------------------
# 1. cohort_metrics
# --------------------------------------------------------------------------

def _cohort_metrics(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    cohort = _cohort(request, required=True)
    start, end = _window(request)
    payments = metrics.payment_metrics(connection, cohort or None, start, end)
    attempts = metrics.attempt_metrics(connection, cohort or None, start, end)
    baseline = detect.baseline_conversion(connection, cohort or None, start, end)
    trailing_start = baseline["trailing_start_epoch"]
    trailing_attempts = metrics.attempt_metrics(connection, cohort or None, trailing_start, start)
    return {
        "as_of": _as_of(connection, end),
        "cohort": cohort,
        "window": _echo_window(request, start, end),
        "payment_metrics": _payment_block(payments, baseline["expected"]),
        "attempt_metrics": _attempt_block(attempts),
        "volume": _volume_block(payments),
        "decline_mix": [
            {"reason": item["reason"], "count": item["count"], "share": _rate(item["share"])}
            for item in metrics.decline_mix(connection, cohort or None, start, end)
        ],
        "baseline": {
            "window": {"start": schema.iso_utc(trailing_start), "end": schema.iso_utc(start)},
            "payment_approval_conversion": _rate(baseline["expected"]),
            "attempt_approval_conversion": _rate(trailing_attempts["approval_conversion"]),
            "method": baseline["method"],
            "trailing_payments": baseline["trailing_payments"],
        },
    }


# --------------------------------------------------------------------------
# 2. cohort_compare
# --------------------------------------------------------------------------

def _cohort_compare(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    cohort = _cohort(request, required=True)
    start, end = _window(request)

    requested = request.get("compare_dimensions")
    if requested is None:
        dimensions = [d for d in schema.DIMENSIONS if d in cohort]
    else:
        if not isinstance(requested, list):
            raise EvidenceError("invalid_input", "compare_dimensions must be an array of strings")
        dimensions = []
        for name in requested:
            dimension = LEVEL_ALIASES.get(name, name)
            if dimension not in schema.DIMENSIONS:
                raise EvidenceError("invalid_input", f"{name!r} is not a cohort dimension")
            if dimension in cohort and dimension not in dimensions:
                dimensions.append(dimension)

    siblings = []
    for dimension in dimensions:
        for value in metrics.dimension_values(connection, dimension, None, start, end):
            if value == cohort[dimension]:
                continue
            sibling = dict(cohort)
            sibling[dimension] = value
            measured = metrics.payment_metrics(connection, sibling, start, end)
            if measured["attempted_payments"] == 0:
                continue
            siblings.append(
                _slice(
                    connection,
                    sibling,
                    start,
                    end,
                    f"same cohort with {dimension} = {value}",
                )
            )

    # The parent is the cohort one step up from every dimension the target
    # names: the merchant it belongs to, or the whole platform when the target
    # is already merchant-wide.
    parent_cohort: dict[str, Any] = {}
    if "merchant_id" in cohort and set(cohort) != {"merchant_id"}:
        parent_cohort = {"merchant_id": cohort["merchant_id"]}

    return {
        "as_of": _as_of(connection, end),
        "window": _echo_window(request, start, end),
        "compare_dimensions": dimensions,
        "target": _slice(connection, cohort, start, end, "affected cohort"),
        "siblings": siblings,
        "parent": _slice(
            connection,
            parent_cohort,
            start,
            end,
            f"{_cohort_label(parent_cohort)} across all remaining dimensions",
        ),
    }


# --------------------------------------------------------------------------
# 3. drilldown
# --------------------------------------------------------------------------

def _drilldown(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    incident_id = _identifier(request, "incident_id")
    record = store.load_incident(connection, incident_id)
    window = _window(request, required=False)

    if record is None and window is None:
        # Nothing to localise and nothing to localise it over. That is a
        # complete answer, not a failure: the path is empty and says why.
        return {
            "as_of": _as_of(connection),
            "incident_id": incident_id,
            "levels": [],
            "stopped_at": None,
            "stop_reason": (
                f"No incident {incident_id} is stored and no window was supplied, "
                "so no localisation path can be reported."
            ),
        }

    start, end = window if window is not None else _record_window(record)
    dimensions = _requested_dimensions(request)
    path = detect.localise(connection, start, end, dimensions=dimensions)

    levels = []
    previous: dict[str, Any] = {}
    for step in path:
        cohort = step["cohort"]
        added = [dimension for dimension in cohort if dimension not in previous]
        level = LEVEL_NAMES.get(added[0], added[0]) if added else ROOT_LEVEL
        levels.append(
            {
                "level": level,
                "cohort": dict(cohort),
                "metrics": {
                    "attempted_payments": step["observed"]["attempted_payments"],
                    "approved_payments": step["observed"]["approved_payments"],
                    "payment_approval_conversion": _rate(step["actual"]),
                    "expected_payment_approval_conversion": _rate(step["expected"]),
                    "absolute_drop": _rate(step["absolute_drop"]),
                },
                "reason": _level_reason(step, level),
            }
        )
        previous = cohort

    return {
        "as_of": _as_of(connection, end),
        "incident_id": incident_id,
        "window": _echo_window(request, start, end),
        "levels": levels,
        "stopped_at": levels[-1]["level"] if levels else None,
        "stop_reason": _stop_reason(path, record, incident_id),
    }


LEVEL_NAMES = {dimension: name for name, dimension in LEVEL_ALIASES.items()}


def _requested_dimensions(request: dict[str, Any]) -> list[str] | None:
    requested = request.get("levels")
    if requested is None:
        return None
    if not isinstance(requested, list):
        raise EvidenceError("invalid_input", "levels must be an array of dimension names")
    dimensions = []
    for name in requested:
        dimension = LEVEL_ALIASES.get(name, name)
        if dimension not in schema.DIMENSIONS:
            raise EvidenceError("invalid_input", f"{name!r} is not a cohort dimension")
        if dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions or None


def _level_reason(step: dict[str, Any], level: str) -> str:
    split = step.get("split")
    if split is None:
        return "Starting point: the whole observed window before any dimension is fixed."
    if split.get("kind") == "qualifying_child":
        return (
            f"{level} is the unique qualifying child: it passes the detection floors "
            "while its parent is diluted below them."
        )
    if split.get("kind") == "observed_singleton":
        return (
            f"{level} is the only observed value inside the affected parent cohort; "
            "this preserves the joint observed slice without asserting causal separation."
        )
    return (
        f"{level} separates from its next sibling ({split['runner_up']}) by "
        f"{split['separation_from_next']} of conversion, so it enters the reported cohort."
    )


def _stop_reason(path: list[dict[str, Any]], record: dict[str, Any] | None, incident_id: str) -> str:
    depth = len(path) - 1
    prefix = "" if record is not None else (
        f"No incident {incident_id} is stored, so the path was localised over the "
        "supplied window alone. "
    )
    if depth >= config.LOCALISE_MAX_DEPTH:
        return (
            f"{prefix}The descent reached the configured maximum depth of "
            f"{config.LOCALISE_MAX_DEPTH} dimensions."
        )
    return (
        f"{prefix}No remaining dimension separates its siblings by at least "
        f"{config.LOCALISE_MIN_SEPARATION} of conversion, so descending further would "
        "report a coincidence as a diagnosis."
    )


def _record_window(record: dict[str, Any]) -> tuple[int, int]:
    """The exact window a stored incident was measured over."""
    detection = record.get("detection") or {}
    window = detection.get("window") or {}
    if isinstance(window, dict) and window.get("start_epoch") is not None:
        return int(window["start_epoch"]), int(window["end_epoch"])
    onset = int(schema.parse_timestamp(record["onset"]).timestamp())
    last_seen = (record.get("persistence") or {}).get("last_observed_at")
    end = int(schema.parse_timestamp(last_seen).timestamp()) if last_seen else onset
    return onset, max(end, onset)


# --------------------------------------------------------------------------
# 4. decline_breakdown
# --------------------------------------------------------------------------

def _decline_breakdown(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    cohort = _cohort(request, required=True)
    start, end = _window(request)
    baseline_window = _window(request, "baseline_window", required=False)
    if baseline_window is None:
        baseline_window = (
            start - config.BASELINE_TRAILING_BUCKETS * config.BUCKET_SECONDS,
            start,
        )
    baseline_start, baseline_end = baseline_window

    observed = metrics.decline_mix(connection, cohort or None, start, end)
    baseline = metrics.decline_mix(connection, cohort or None, baseline_start, baseline_end)
    baseline_shares = {item["reason"]: item["share"] for item in baseline}
    baseline_failed = sum(item["count"] for item in baseline)
    observed_failed = sum(item["count"] for item in observed)

    reasons = []
    for item in observed:
        baseline_share = baseline_shares.get(item["reason"], 0.0) if baseline_failed else None
        reasons.append(
            {
                "reason": item["reason"],
                "count": item["count"],
                "share": _rate(item["share"]),
                "baseline_share": _rate(baseline_share),
                "shift": _rate(None if baseline_share is None else item["share"] - baseline_share),
            }
        )
    # A reason that has disappeared is evidence too, so the baseline-only
    # reasons are reported at zero rather than omitted.
    seen = {item["reason"] for item in observed}
    for item in baseline:
        if item["reason"] in seen:
            continue
        reasons.append(
            {
                "reason": item["reason"],
                "count": 0,
                "share": 0.0,
                "baseline_share": _rate(item["share"]),
                "shift": _rate(-item["share"]),
            }
        )

    return {
        "as_of": _as_of(connection, end),
        "cohort": cohort,
        "window": _echo_window(request, start, end),
        "normalised_denominator": "failed_attempts",
        "failed_attempts": observed_failed,
        "reasons": reasons,
        "baseline": {
            "window": {"start": schema.iso_utc(baseline_start), "end": schema.iso_utc(baseline_end)},
            "failed_attempts": baseline_failed,
        },
    }


# --------------------------------------------------------------------------
# 5. retry_stats
# --------------------------------------------------------------------------

def _retry_stats(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    cohort = _cohort(request, required=True)
    start, end = _window(request)
    retries = metrics.retry_profile(connection, cohort or None, start, end)
    return {
        "as_of": _as_of(connection, end),
        "cohort": cohort,
        "window": _echo_window(request, start, end),
        "payments": retries["payments"],
        "attempts": retries["attempts"],
        "retried_payments": retries["retried_payments"],
        "retry_depth": retries["retry_depth"],
        "attempts_per_payment": _rate(retries["attempts_per_payment"]),
        "retry_amplification_factor": _rate(retries["retry_amplification_factor"]),
        "queue": metrics.queue_profile(connection, cohort or None, start, end),
    }


# --------------------------------------------------------------------------
# 6. operational_metrics
# --------------------------------------------------------------------------

def _operational_metrics(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    target = _object(request, "target", required=True)
    start, end = _window(request)
    kind = target.get("kind", "cohort")
    if kind not in ("cohort", "service"):
        raise EvidenceError("invalid_input", "target.kind must be 'cohort' or 'service'")

    filters: dict[str, Any] = {}
    for key, value in target.items():
        if key == "kind" or value is None:
            continue
        column = "service_id" if key in ("service", "service_id") else key
        if column not in metrics.FILTERABLE:
            raise EvidenceError("invalid_input", f"{key!r} is not a filterable target dimension")
        filters[column] = value

    measured = metrics.operational_metrics(connection, filters or None, start, end)
    attempts = measured["attempts"]
    failure_rate = (
        ((measured["errors"] + measured["timeouts"]) / attempts) if attempts else None
    )
    if attempts == 0:
        status = "unobserved"
    elif failure_rate >= config.OPERATIONAL_DEGRADED_RATE:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "as_of": _as_of(connection, end),
        "target": dict(target),
        "window": _echo_window(request, start, end),
        "attempts": attempts,
        "latency_ms": measured["latency_ms"],
        "error_rate": _rate(measured["error_rate"]),
        "timeout_rate": _rate(measured["timeout_rate"]),
        "service_health": {
            "status": status,
            "observed_failure_rate": _rate(failure_rate),
            "criterion": (
                "derived from first-party attempts: degraded when the combined error and "
                f"timeout rate reaches {config.OPERATIONAL_DEGRADED_RATE}, unobserved with no attempts"
            ),
        },
        # Runtime health is the one thing attempts cannot answer. It comes from
        # W1's ops.telemetry samples when the consumer has any, and stays an
        # honest `unobserved` when it does not - which is what the file-based
        # demo path reports, unchanged.
        "runtime_health": metrics.runtime_health(
            connection,
            [filters["service_id"]] if filters.get("service_id") else measured["services"],
            start,
            end,
        ),
        "deployment": {
            "service": measured["services"][0] if len(measured["services"]) == 1 else None,
            "deployment_id": (
                measured["deployments"][0] if len(measured["deployments"]) == 1 else None
            ),
            "observed_services": measured["services"],
            "observed_deployment_ids": measured["deployments"],
        },
        "queue": metrics.queue_profile(connection, filters or None, start, end),
    }


# --------------------------------------------------------------------------
# 7. confounding_check
# --------------------------------------------------------------------------

def _confounding_check(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    dimension_a = _identifier(request, "dimension_a")
    dimension_b = _identifier(request, "dimension_b")
    for name in (dimension_a, dimension_b):
        if LEVEL_ALIASES.get(name, name) not in schema.DIMENSIONS:
            raise EvidenceError("invalid_input", f"{name!r} is not a cohort dimension")
    dimension_a = LEVEL_ALIASES.get(dimension_a, dimension_a)
    dimension_b = LEVEL_ALIASES.get(dimension_b, dimension_b)
    cohort = _cohort(request)
    start, end = _window(request)

    result = metrics.confounding(connection, dimension_a, dimension_b, cohort or None, start, end)
    rows = result["cross_tabulation"]["rows"]

    mappings: dict[str, list[Any]] = {}
    for row in rows:
        mappings.setdefault(str(row[dimension_a]), [])
        mappings.setdefault(str(row[dimension_b]), [])
        if row[dimension_b] not in mappings[str(row[dimension_a])]:
            mappings[str(row[dimension_a])].append(row[dimension_b])
        if row[dimension_a] not in mappings[str(row[dimension_b])]:
            mappings[str(row[dimension_b])].append(row[dimension_a])

    if not rows:
        interpretation = (
            f"No attempts were observed for {dimension_a} and {dimension_b} in this window, "
            "so their separability cannot be established either way."
        )
    elif result["structurally_inseparable"]:
        interpretation = (
            f"The data cannot discriminate a {dimension_a} cause from a {dimension_b} cause: "
            "each observed value of one appears with exactly one value of the other."
        )
    else:
        interpretation = (
            f"{dimension_a} and {dimension_b} are separable in this window: at least one value "
            "of one appears with more than one value of the other."
        )

    return {
        "as_of": _as_of(connection, end),
        "dimension_a": dimension_a,
        "dimension_b": dimension_b,
        "cohort": cohort,
        "window": _echo_window(request, start, end),
        "structurally_inseparable": result["structurally_inseparable"],
        "criterion": result["criterion"],
        "cross_tabulation": result["cross_tabulation"],
        "observed_mappings": {key: mappings[key] for key in sorted(mappings)},
        "interpretation": interpretation,
    }


# --------------------------------------------------------------------------
# 8. incident_history
# --------------------------------------------------------------------------

def _incident_history(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    merchant_id = _identifier(request, "merchant_id")
    cohort_filter = _cohort(request)
    window = _window(request, required=False)

    incidents = []
    for record in store.list_incidents(connection):
        affected = record.get("affected_cohort") or {}
        if affected.get("merchant_id") not in (merchant_id, None):
            continue
        if any(affected.get(key) != value for key, value in cohort_filter.items()):
            continue
        onset = int(schema.parse_timestamp(record["onset"]).timestamp())
        if window is not None and not (window[0] <= onset < window[1]):
            continue
        incidents.append(_history_entry(record, onset))

    lookback_days = None if window is None else round((window[1] - window[0]) / 86400.0, 4)
    pattern = (
        " and ".join(f"{key}={cohort_filter[key]}" for key in sorted(cohort_filter))
        if cohort_filter
        else f"{merchant_id} across all dimensions"
    )
    return {
        "as_of": _as_of(connection, window[1] if window else None),
        "merchant_id": merchant_id,
        "cohort_filter": cohort_filter,
        "window": _echo_window(request, *window) if window else None,
        "incidents": incidents,
        "recurrence": {
            "prior_matching_incidents": len(incidents),
            "lookback_days": lookback_days,
            "pattern": pattern,
        },
    }


def _history_entry(record: dict[str, Any], onset: int) -> dict[str, Any]:
    change = record.get("change") or {}
    lifecycle = record.get("lifecycle_state")
    last_seen = (record.get("persistence") or {}).get("last_observed_at")
    cohort = record.get("affected_cohort") or {}
    return {
        "incident_id": record.get("incident_id"),
        "onset": record.get("onset"),
        "resolved_at": last_seen if lifecycle == "resolved" else None,
        "lifecycle_state": lifecycle,
        "severity": record.get("severity"),
        "cohort": cohort,
        "payment_approval_conversion": {
            "expected": change.get("expected"),
            "actual": change.get("actual"),
        },
        "summary": (
            f"{record.get('severity')} severity incident on {_cohort_label(cohort)}: "
            f"{change.get('metric', 'payment_approval_conversion')} "
            f"{change.get('expected')} to {change.get('actual')} from {record.get('onset')}."
        ),
    }


# --------------------------------------------------------------------------
# 9. financial_impact
# --------------------------------------------------------------------------

def _financial_impact(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    incident_id = _identifier(request, "incident_id")
    record = store.load_incident(connection, incident_id)
    window = _window(request, required=False)

    if record is None:
        start, end = window if window is not None else (0, 0)
        return {
            "as_of": _as_of(connection, end if window is not None else None),
            "incident_id": incident_id,
            "window": _echo_window(request, start, end) if window is not None else None,
            "attempted_value": _money(0.0),
            "expected_approval_rate": None,
            "actual_approval_rate": None,
            "expected_approved_payments": 0,
            "actual_approved_payments": 0,
            "estimated_lost_approved_volume": {
                "payments": 0,
                "amount": 0.0,
                "currency": config.REPORTING_CURRENCY,
            },
            "gmv_at_risk": _money(0.0),
            "loss_per_hour": _money(0.0),
            "assumptions": [
                f"No incident {incident_id} is stored, so no impact is claimed for it.",
                "GMV at risk is an estimate of approved volume not captured, "
                "not a platform-revenue claim.",
            ],
        }

    cohort = record.get("affected_cohort") or {}
    start, end = window if window is not None else _record_window(record)
    detection = record.get("detection") or {}
    expected = detection.get("expected_conversion")
    if expected is None:
        expected = (record.get("change") or {}).get("expected")
    if expected is None:
        raise EvidenceError(
            "incomplete_incident",
            f"incident {incident_id} carries no expected conversion to price against",
        )

    payments = metrics.payment_metrics(connection, cohort or None, start, end)
    impact = metrics.financial_impact(connection, cohort or None, start, end, float(expected))
    return {
        "as_of": _as_of(connection, end),
        "incident_id": incident_id,
        "cohort": dict(cohort),
        "window": {
            **_echo_window(request, start, end),
            "duration_seconds": max(0, end - start),
        },
        "expected_approved_payments": int(round(float(expected) * payments["attempted_payments"])),
        "actual_approved_payments": payments["approved_payments"],
        **impact,
    }


# --------------------------------------------------------------------------
# 10. metric_series
# --------------------------------------------------------------------------

def _metric_series(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    cohort = _cohort(request)
    start, end = _window(request)
    metric = request.get("metric", "payment_approval_conversion")
    if metric not in SERIES_METRICS:
        raise EvidenceError(
            "invalid_input",
            f"{metric!r} is not a published series metric; supported metrics are "
            f"{', '.join(SERIES_METRICS)}",
        )
    bucket_seconds = request.get("bucket_seconds", config.BUCKET_SECONDS)
    if not isinstance(bucket_seconds, int) or isinstance(bucket_seconds, bool) or bucket_seconds <= 0:
        raise EvidenceError("invalid_input", "bucket_seconds must be a positive integer")

    # Only buckets that have fully closed behind the watermark are reported. A
    # partial trailing bucket would read as a collapse in conversion that is
    # really just the minute not being over yet.
    mark = watermark(connection)
    horizon = min(end, mark)

    points = []
    if horizon > start:
        payments = {
            point["bucket_start_epoch"]: point
            for point in metrics.timeseries(connection, cohort or None, start, horizon, bucket_seconds)
        }
        attempts = {
            point["bucket_start_epoch"]: point
            for point in metrics.attempt_timeseries(
                connection, cohort or None, start, horizon, bucket_seconds
            )
        }
        for bucket in sorted(set(payments) | set(attempts)):
            if bucket + bucket_seconds > horizon:
                continue
            payment = payments.get(bucket, _EMPTY_PAYMENT_BUCKET)
            attempt = attempts.get(bucket, _EMPTY_ATTEMPT_BUCKET)
            points.append(
                {
                    "bucket_start": schema.iso_utc(bucket),
                    "bucket_end": schema.iso_utc(bucket + bucket_seconds),
                    "value": _series_value(metric, payment, attempt),
                    "samples": (
                        attempt["attempts"]
                        if metric in ("attempt_approval_conversion", "attempts", "failed_attempts")
                        else payment["attempted_payments"]
                    ),
                }
            )

    return {
        "as_of": _as_of(connection, end),
        "cohort": cohort,
        "window": _echo_window(request, start, end),
        "metric": metric,
        "bucket_seconds": bucket_seconds,
        "watermark": schema.iso_utc(mark),
        "measured_through": schema.iso_utc(max(start, horizon)),
        "points": points,
    }


_EMPTY_PAYMENT_BUCKET = {
    "attempted_payments": 0,
    "approved_payments": 0,
    "approval_conversion": None,
    "attempted_value_usd": 0.0,
}
_EMPTY_ATTEMPT_BUCKET = {
    "attempts": 0,
    "approved_attempts": 0,
    "failed_attempts": 0,
    "approval_conversion": None,
}


def _series_value(metric: str, payment: dict[str, Any], attempt: dict[str, Any]) -> Any:
    if metric == "payment_approval_conversion":
        return _rate(payment["approval_conversion"])
    if metric == "attempt_approval_conversion":
        return _rate(attempt["approval_conversion"])
    if metric == "attempted_payments":
        return payment["attempted_payments"]
    if metric == "approved_payments":
        return payment["approved_payments"]
    if metric == "attempts":
        return attempt["attempts"]
    if metric == "failed_attempts":
        return attempt["failed_attempts"]
    if metric == "attempted_value_usd":
        return payment["attempted_value_usd"]
    payments = payment["attempted_payments"]
    return _rate(attempt["attempts"] / payments) if payments else None


# --------------------------------------------------------------------------

def _identifier(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("invalid_input", f"{key} is required and must be a non-empty string")
    return value


# --------------------------------------------------------------------------
# 12. ingest_health
# --------------------------------------------------------------------------

def _ingest_health(connection: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    """Answer "is anything actually arriving?" from the store alone.

    Every other tool measures the payments. This one measures the measuring:
    how many records survived normalisation into the store, how many were
    refused and why, how recent the newest one is, and how far that newest
    event sits ahead of the point measurement is complete to. It is the only
    tool whose subject is the pipeline rather than the traffic, which is why it
    takes no cohort and no window - a freshness figure narrowed to a window is
    not a freshness figure.

    **`duplicates` is deliberately absent.** At-least-once delivery is turned
    into exactly-once counting by ``INSERT OR IGNORE`` on `event_id`
    (`store.write_batch`), so a redelivered record leaves no trace in the
    store: the counter exists only in the consumer's in-memory `Progress` for
    the length of one run and is reported on its stdout. There is no honest way
    to recover it here, so it is named in `not_measured` rather than guessed
    at. `not_measured` is a statement about this tool, not a counter.

    **The event-time fields describe the canonical attempt stream.**
    `newest_event_at`, `watermark`, `as_of` and `lag_seconds` all read
    `store.window_bounds`, which is the attempt table - the same stream `as_of`
    has meant on every C2 tool since the first one, and not a meaning this tool
    is free to redefine. Telemetry and closed-payment rows are stored beside
    attempts and are counted in `stored`, but they do not move the watermark.
    That would leave a store holding only telemetry samples reporting "nothing
    observed" while it plainly holds something, so `newest_by_kind` reports each
    kind's own newest event time separately. It is a second set of readings, not
    a redefinition of the first.

    **`rejected` and `dead_letter.count` are one measurement, not two.** A
    refused record is dead-lettered in the same statement that rejects it, so
    the two fields are equal by construction. Both are published because a
    caller asking "was anything rejected" and a caller asking "what is in the
    dead-letter queue" are asking the same question of this store and should
    not have to know that.
    """
    if request:
        # No input is defined, so an argument is a caller believing in a filter
        # that does not exist. Refusing beats silently answering a wider
        # question than the one asked.
        raise EvidenceError(
            "invalid_input",
            "ingest_health takes no input; it reports the whole store, "
            f"but received {', '.join(sorted(map(str, request)))}",
        )

    stored = store.stored_counts(connection)
    bounds = store.window_bounds(connection)
    mark = watermark(connection)

    rows = connection.execute(
        "SELECT reason, COUNT(*) AS n FROM dead_letter "
        "GROUP BY reason ORDER BY n DESC, reason ASC"
    ).fetchall()
    sources = connection.execute(
        "SELECT source, COUNT(*) AS n FROM dead_letter "
        "GROUP BY source ORDER BY n DESC, source ASC"
    ).fetchall()
    rejected = sum(int(row["n"]) for row in rows)

    return {
        "as_of": _as_of(connection),
        "watermark": schema.iso_utc(mark),
        # The headline: normalised payment attempts the store holds, post-dedupe.
        "accepted": stored["attempt"],
        "stored": {
            "attempts": stored["attempt"],
            "telemetry_samples": stored["telemetry"],
            "payments_closed": stored["closed"],
        },
        "rejected": rejected,
        "dead_letter": {
            "count": rejected,
            "distinct_reasons": len(rows),
            "reasons": [
                {"reason": row["reason"], "count": int(row["n"])}
                for row in rows[:DEAD_LETTER_REASON_LIMIT]
            ],
            "by_source": [
                {"source": row["source"], "count": int(row["n"])} for row in sources
            ],
        },
        "oldest_event_at": schema.iso_utc(bounds[0]) if bounds else None,
        "newest_event_at": schema.iso_utc(bounds[1]) if bounds else None,
        # Per-kind newest event time. `newest_event_at` above is the canonical
        # attempt stream and is what the watermark is cut from; these are read
        # so that a store holding telemetry and no payments cannot report
        # "nothing observed" while holding something.
        "newest_by_kind": {
            name: _newest_event_at(connection, store.KINDS[kind], column)
            for name, kind, column in (
                ("attempts", "attempt", KIND_TIME_COLUMNS["attempt"]),
                ("telemetry_samples", "telemetry", KIND_TIME_COLUMNS["telemetry"]),
                ("payments_closed", "closed", KIND_TIME_COLUMNS["closed"]),
            )
        },
        # How far the newest observed event sits ahead of the watermark. This
        # is event time against event time, never against the wall clock: it
        # says how much of what has arrived is not yet measured, and it stays
        # the same on a replay. It is not "how long since a record arrived",
        # and a caller must not present it as one.
        "lag_seconds": max(0, bounds[1] - mark) if bounds else None,
        "lateness_grace_seconds": config.LATENESS_GRACE_SECONDS,
        "not_measured": {
            "duplicates": (
                "redelivered records are dropped by INSERT OR IGNORE on event_id and "
                "leave no row behind; the count lives only in the consumer run that "
                "saw them, so the store cannot report it"
            ),
        },
    }



def _newest_event_at(connection: sqlite3.Connection, table: str, column: str) -> str | None:
    """The newest event time in one stored table, or None where it holds none.

    Read-only, and deliberately here rather than in `store`: the watermark's
    definition is the attempt stream and must not start depending on what else
    happens to be stored, or every tool's `as_of` moves with telemetry.
    """
    row = connection.execute(f"SELECT MAX({column}) AS hi FROM {table}").fetchone()
    if row is None or row["hi"] is None:
        return None
    return schema.iso_utc(int(row["hi"]))


_HANDLERS = {
    "cohort_metrics": _cohort_metrics,
    "cohort_compare": _cohort_compare,
    "drilldown": _drilldown,
    "decline_breakdown": _decline_breakdown,
    "retry_stats": _retry_stats,
    "operational_metrics": _operational_metrics,
    "confounding_check": _confounding_check,
    "incident_history": _incident_history,
    "financial_impact": _financial_impact,
    "metric_series": _metric_series,
    "ingest_health": _ingest_health,
}
