"""SQLite persistence for investigation results and evidence trails.

L4 shares the detector's relational SQLite file. Connections use WAL and a
busy timeout, and claiming a row is one guarded UPDATE so concurrent runners
cannot both take the same watch or detected incident.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path("state/clearwave.db")
OUTCOMES = (
    "diagnosed",
    "ambiguous",
    "insufficient_evidence",
    "agent_unavailable",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS incident (
    incident_id     TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    record          TEXT NOT NULL,
    cohort_key      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    severity_score  REAL NOT NULL,
    lifecycle_state TEXT NOT NULL,
    onset_epoch     INTEGER NOT NULL,
    last_seen_epoch INTEGER NOT NULL,
    config_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS incident_cohort ON incident (cohort_key);

CREATE TABLE IF NOT EXISTS investigation_result (
    incident_id     TEXT NOT NULL,
    result_version  INTEGER NOT NULL,
    outcome         TEXT NOT NULL,
    result          TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    duration_ms     REAL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (incident_id, result_version)
);
CREATE INDEX IF NOT EXISTS investigation_result_latest
    ON investigation_result (incident_id, result_version DESC);

CREATE TABLE IF NOT EXISTS evidence_trail (
    incident_id     TEXT NOT NULL,
    result_version  INTEGER NOT NULL,
    sequence        INTEGER NOT NULL,
    query_id        TEXT NOT NULL,
    tool            TEXT NOT NULL,
    parameters      TEXT NOT NULL,
    response        TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    duration_ms     REAL NOT NULL,
    outcome         TEXT NOT NULL,
    executed        INTEGER NOT NULL,
    PRIMARY KEY (incident_id, result_version, sequence)
);
CREATE INDEX IF NOT EXISTS evidence_trail_query ON evidence_trail (query_id);

CREATE TABLE IF NOT EXISTS investigation_bound (
    incident_id           TEXT PRIMARY KEY,
    evidence_fingerprint  TEXT NOT NULL,
    model_calls           INTEGER NOT NULL DEFAULT 0,
    last_claimed_from     TEXT NOT NULL,
    last_investigated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_claim (
    incident_id   TEXT PRIMARY KEY,
    claimed_at    TEXT NOT NULL,
    claimed_from  TEXT NOT NULL
);
"""

CLAIMABLE_STATES = ("detected", "watching")
_CLAIMABLE_SQL = "lifecycle_state IN ('detected', 'watching')"
# One investigation may run up to agent.DEFAULT_TIMEOUT_SECONDS (300). The lease
# is that bound plus a short grace so a live call is not stolen, and a crashed
# claim is not stuck forever.
CLAIM_LEASE_SECONDS = 330
_BOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS investigation_bound (
    incident_id           TEXT PRIMARY KEY,
    evidence_fingerprint  TEXT NOT NULL,
    model_calls           INTEGER NOT NULL DEFAULT 0,
    last_claimed_from     TEXT NOT NULL,
    last_investigated_at  TEXT NOT NULL
);
"""


def ensure_bound_table(connection: sqlite3.Connection) -> None:
    """Create the cost-bound table on stores the detector opened first."""
    connection.executescript(_BOUND_SCHEMA)


def prepare(connection: sqlite3.Connection) -> None:
    """Ensure L4 tables exist on a connection the detector may have opened first."""
    connection.executescript(SCHEMA)


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Open an L4 store, creating its parent directory and tables if needed."""
    target = Path(path)
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    prepare(connection)
    return connection


def claim_incident(connection: sqlite3.Connection, incident_id: str) -> bool:
    """Atomically move one watch or detected incident to ``investigating``.

    The lifecycle predicate is part of the UPDATE. SQLite serialises writers,
    so exactly one concurrent caller can observe a changed row count. A watch
    and a detected incident share this guard so two runners cannot split one
    cohort into two investigations. The claim lease is written in the same
    transaction so a crash before persist can be reclaimed.
    """
    prepare(connection)
    with connection:
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            return False
        previous = str(row["lifecycle_state"])
        if previous not in CLAIMABLE_STATES:
            return False
        cursor = connection.execute(
            "UPDATE incident SET lifecycle_state = 'investigating' "
            "WHERE incident_id = ? AND lifecycle_state = ?",
            (incident_id, previous),
        )
        if cursor.rowcount != 1:
            return False
        connection.execute(
            """INSERT OR REPLACE INTO investigation_claim
               (incident_id, claimed_at, claimed_from) VALUES (?, ?, ?)""",
            (incident_id, _utc_now(), previous),
        )
    return True


claim_detected_incident = claim_incident
claim_detected = claim_incident


def reclaim_expired_claims(
    connection: sqlite3.Connection,
    *,
    now: str | None = None,
    lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> list[str]:
    """Return abandoned ``investigating`` rows to the state they were claimed from.

    A crash after the claim UPDATE and before persist leaves the demo pointing
    at a record that ``_pending_rows`` would otherwise never see again. An
    expired lease, or an investigating row with no lease at all, is restored
    so the next poll can claim it. A live claim inside the lease is left alone.
    """
    prepare(connection)
    cutoff = _iso_minus(now or _utc_now(), lease_seconds)
    reclaimed: list[str] = []
    with connection:
        expired = connection.execute(
            """SELECT c.incident_id, c.claimed_from
               FROM investigation_claim AS c
               JOIN incident AS i ON i.incident_id = c.incident_id
               WHERE i.lifecycle_state = 'investigating'
                 AND c.claimed_at <= ?""",
            (cutoff,),
        ).fetchall()
        orphans = connection.execute(
            """SELECT i.incident_id, i.record
               FROM incident AS i
               LEFT JOIN investigation_claim AS c ON c.incident_id = i.incident_id
               WHERE i.lifecycle_state = 'investigating'
                 AND c.incident_id IS NULL"""
        ).fetchall()
        pending = [(str(row["incident_id"]), _restore_state(row["claimed_from"])) for row in expired]
        for row in orphans:
            record = json.loads(row["record"]) if row["record"] else {}
            previous = record.get("lifecycle_state") if isinstance(record, Mapping) else None
            pending.append((str(row["incident_id"]), _restore_state(previous)))
        for incident_id, restore in pending:
            cursor = connection.execute(
                "UPDATE incident SET lifecycle_state = ? "
                "WHERE incident_id = ? AND lifecycle_state = 'investigating'",
                (restore, incident_id),
            )
            if cursor.rowcount != 1:
                continue
            connection.execute(
                "DELETE FROM investigation_claim WHERE incident_id = ?",
                (incident_id,),
            )
            reclaimed.append(incident_id)
    return reclaimed


def append_trail_entry(
    connection: sqlite3.Connection,
    incident_id: str,
    entry: Mapping[str, Any],
    version: int = 1,
) -> int:
    """Persist one trail entry and return its sequence number."""
    result_version = _version_number(version)
    with connection:
        sequence = int(entry.get("sequence") or _next_sequence(connection, incident_id, result_version))
        connection.execute(
            """INSERT OR REPLACE INTO evidence_trail
               (incident_id, result_version, sequence, query_id, tool,
                parameters, response, timestamp, duration_ms, outcome, executed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                incident_id,
                result_version,
                sequence,
                str(entry.get("query_id", "")),
                str(entry.get("tool", "")),
                _json(entry.get("parameters", {})),
                _json(entry.get("response", {})),
                str(entry.get("timestamp", _utc_now())),
                float(entry.get("duration_ms", 0.0)),
                str(entry.get("outcome", "unknown")),
                int(bool(entry.get("executed", True))),
            ),
        )
    return sequence


def persist_result(
    connection: sqlite3.Connection,
    incident_id: str,
    result: Mapping[str, Any],
    outcome: str,
    *,
    version: int | str | None = None,
    trail: Iterable[Mapping[str, Any]] | Any | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    duration_ms: float | None = None,
    resume_state: str | None = None,
    evidence_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Persist a versioned C4 result and its complete trail.

    When no version is supplied, the next integer version is allocated in the
    same transaction as the result insert. A detected incident completes to
    ``diagnosed``. A watch returns to ``watching`` so it stays off the paging
    allowlist and the detector can still upgrade the same row when floors pass.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if not isinstance(result, Mapping):
        raise TypeError("result must be a mapping")

    prepare(connection)
    with connection:
        result_version = (
            _next_version(connection, incident_id)
            if version is None
            else _version_number(version)
        )
        now = _utc_now()
        connection.execute(
            """INSERT INTO investigation_result
               (incident_id, result_version, outcome, result, started_at,
                completed_at, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                incident_id,
                result_version,
                outcome,
                _json(result),
                started_at,
                completed_at or now,
                None if duration_ms is None else float(duration_ms),
                now,
            ),
        )
        for index, entry in enumerate(_entries(trail), start=1):
            sequence = int(entry.get("sequence") or index)
            connection.execute(
                """INSERT OR REPLACE INTO evidence_trail
                   (incident_id, result_version, sequence, query_id, tool,
                    parameters, response, timestamp, duration_ms, outcome, executed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    result_version,
                    sequence,
                    str(entry.get("query_id", "")),
                    str(entry.get("tool", "")),
                    _json(entry.get("parameters", {})),
                    _json(entry.get("response", {})),
                    str(entry.get("timestamp", now)),
                    float(entry.get("duration_ms", 0.0)),
                    str(entry.get("outcome", "unknown")),
                    int(bool(entry.get("executed", True))),
                ),
            )
        if resume_state == "watching":
            connection.execute(
                "UPDATE incident SET lifecycle_state = 'watching' "
                "WHERE incident_id = ? AND lifecycle_state IN ('investigating', 'watching')",
                (incident_id,),
            )
        else:
            connection.execute(
                "UPDATE incident SET lifecycle_state = 'diagnosed' "
                "WHERE incident_id = ? AND lifecycle_state IN ('detected', 'investigating')",
                (incident_id,),
            )
        connection.execute(
            "DELETE FROM investigation_claim WHERE incident_id = ?",
            (incident_id,),
        )
        if evidence_fingerprint is not None:
            ensure_bound_table(connection)
            claimed_from = resume_state if resume_state in CLAIMABLE_STATES else "detected"
            connection.execute(
                """INSERT INTO investigation_bound
                   (incident_id, evidence_fingerprint, model_calls,
                    last_claimed_from, last_investigated_at)
                   VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(incident_id) DO UPDATE SET
                     evidence_fingerprint = excluded.evidence_fingerprint,
                     model_calls = investigation_bound.model_calls + 1,
                     last_claimed_from = excluded.last_claimed_from,
                     last_investigated_at = excluded.last_investigated_at""",
                (incident_id, evidence_fingerprint, claimed_from, now),
            )

    return read_result(connection, incident_id, result_version) or {}


def read_result(
    connection: sqlite3.Connection,
    incident_id: str,
    version: int | str | None = None,
) -> dict[str, Any] | None:
    """Read one result, including all persisted trail entries in order."""
    if version is None:
        row = connection.execute(
            "SELECT * FROM investigation_result "
            "WHERE incident_id = ? ORDER BY result_version DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM investigation_result "
            "WHERE incident_id = ? AND result_version = ?",
            (incident_id, _version_number(version)),
        ).fetchone()
    if row is None:
        return None

    entries = connection.execute(
        """SELECT sequence, query_id, tool, parameters, response, timestamp,
                  duration_ms, outcome, executed
           FROM evidence_trail
           WHERE incident_id = ? AND result_version = ?
           ORDER BY sequence ASC""",
        (incident_id, row["result_version"]),
    ).fetchall()
    return {
        "incident_id": row["incident_id"],
        "version": row["result_version"],
        "outcome": row["outcome"],
        "result": json.loads(row["result"]),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "duration_ms": row["duration_ms"],
        "trail": [_trail_row(entry) for entry in entries],
    }


load_result = read_result
save_result = persist_result


def insert_incident(
    connection: sqlite3.Connection,
    incident: Mapping[str, Any],
    *,
    lifecycle_state: str = "detected",
    config_version: str = "unknown",
) -> None:
    """Insert a C3 incident for a local runner or deterministic test."""
    incident_id = str(incident["incident_id"])
    record = dict(incident)
    record["lifecycle_state"] = lifecycle_state
    cohort = incident.get("affected_cohort", {})
    severity = str(incident.get("severity", "low"))
    financial = incident.get("financial_impact", {})
    loss_rate = financial.get("loss_per_hour", {}) if isinstance(financial, Mapping) else {}
    onset = _epoch(incident.get("onset"))
    last_seen = _epoch(
        incident.get("persistence", {}).get("last_observed_at")
        if isinstance(incident.get("persistence"), Mapping)
        else None
    ) or onset
    with connection:
        connection.execute(
            """INSERT INTO incident
               (incident_id, created_at, record, cohort_key, severity,
                severity_score, lifecycle_state, onset_epoch, last_seen_epoch,
                config_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                incident_id,
                _utc_now(),
                _json(record),
                _json(cohort),
                severity,
                float(loss_rate.get("amount", 0.0)) if isinstance(loss_rate, Mapping) else 0.0,
                lifecycle_state,
                onset,
                last_seen,
                config_version,
            ),
        )


def _entries(trail: Any) -> list[Mapping[str, Any]]:
    if trail is None:
        return []
    if hasattr(trail, "entries"):
        return [dict(entry) for entry in trail.entries]
    return [dict(entry) for entry in trail]


def _trail_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sequence": row["sequence"],
        "query_id": row["query_id"],
        "tool": row["tool"],
        "parameters": json.loads(row["parameters"]),
        "response": json.loads(row["response"]),
        "timestamp": row["timestamp"],
        "duration_ms": row["duration_ms"],
        "outcome": row["outcome"],
        "executed": bool(row["executed"]),
    }


def _next_sequence(connection: sqlite3.Connection, incident_id: str, version: int) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
        "FROM evidence_trail WHERE incident_id = ? AND result_version = ?",
        (incident_id, version),
    ).fetchone()
    return int(row["next_sequence"])


def _next_version(connection: sqlite3.Connection, incident_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(result_version), 0) + 1 AS next_version "
        "FROM investigation_result WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    return int(row["next_version"])


def _version_number(value: int | str) -> int:
    if isinstance(value, bool):
        raise ValueError("version must be a positive integer")
    text = str(value).strip().lower()
    if text.startswith("v"):
        text = text[1:]
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError("version must be a positive integer") from exc
    if number < 1:
        raise ValueError("version must be a positive integer")
    return number


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_minus(value: str, seconds: int) -> str:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    earlier = moment - timedelta(seconds=max(0, int(seconds)))
    return earlier.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _restore_state(value: Any) -> str:
    state = str(value or "")
    if state in CLAIMABLE_STATES:
        return state
    return "detected"


def _epoch(value: Any) -> int:
    if not isinstance(value, str) or not value:
        return 0
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def evidence_fingerprint(record: Mapping[str, Any]) -> str:
    """Digest of the evidence that should trigger a fresh investigation.

    Lifecycle, cohort, measured change, severity and watch reasons are in.
    Persistence duration, last-seen and onset walking are out: those move on
    every sweep even when nothing about the deviation has changed.
    """
    change = record.get("change") if isinstance(record.get("change"), Mapping) else {}
    detection = record.get("detection") if isinstance(record.get("detection"), Mapping) else {}
    watch = detection.get("watch") if isinstance(detection.get("watch"), Mapping) else {}
    indicators = watch.get("leading_indicators") if isinstance(watch.get("leading_indicators"), Mapping) else {}
    payload = {
        "lifecycle_state": str(record.get("lifecycle_state") or ""),
        "cohort": record.get("affected_cohort") or {},
        "metric": change.get("metric"),
        "expected": _quantize(change.get("expected"), 4),
        "actual": _quantize(change.get("actual"), 4),
        "absolute_delta": _quantize(change.get("absolute_delta"), 4),
        "severity": record.get("severity"),
        "watch_reasons": sorted(str(item) for item in (watch.get("reasons") or [])),
        "degraded_leading_indicators": sorted(
            str(item) for item in (watch.get("degraded_leading_indicators") or [])
        ),
        "leading_indicator_ratios": {
            str(name): _indicator_ratio(values)
            for name, values in sorted(indicators.items())
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def read_bound_fingerprint(connection: sqlite3.Connection, incident_id: str) -> str | None:
    """Fingerprint of the last investigation of this record, if any."""
    ensure_bound_table(connection)
    row = connection.execute(
        "SELECT evidence_fingerprint FROM investigation_bound WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["evidence_fingerprint"])


def model_call_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """How many model investigations this store has actually spent."""
    ensure_bound_table(connection)
    rows = connection.execute(
        "SELECT incident_id, model_calls, last_claimed_from, last_investigated_at "
        "FROM investigation_bound ORDER BY incident_id"
    ).fetchall()
    by_incident = [
        {
            "incident_id": str(row["incident_id"]),
            "model_calls": int(row["model_calls"]),
            "last_claimed_from": str(row["last_claimed_from"]),
            "last_investigated_at": str(row["last_investigated_at"]),
        }
        for row in rows
    ]
    return {
        "total": sum(item["model_calls"] for item in by_incident),
        "by_incident": by_incident,
    }


def _quantize(value: Any, digits: int) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return value


def _indicator_ratio(values: Any) -> Any:
    if isinstance(values, Mapping):
        return _quantize(values.get("ratio"), 2)
    return _quantize(values, 2)
