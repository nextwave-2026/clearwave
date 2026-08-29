"""SQLite persistence for investigation results and evidence trails.

L4 shares the detector's relational SQLite file. Connections use WAL and a
busy timeout, and claiming an incident is one guarded UPDATE so concurrent
runners cannot both take a detected incident.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
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
"""


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
    connection.executescript(SCHEMA)
    return connection


def claim_incident(connection: sqlite3.Connection, incident_id: str) -> bool:
    """Atomically move one detected incident to ``investigating``.

    The lifecycle predicate is part of the UPDATE. SQLite serialises writers,
    so exactly one concurrent caller can observe a changed row count.
    """
    with connection:
        cursor = connection.execute(
            "UPDATE incident SET lifecycle_state = 'investigating' "
            "WHERE incident_id = ? AND lifecycle_state = 'detected'",
            (incident_id,),
        )
    return cursor.rowcount == 1


claim_detected_incident = claim_incident
claim_detected = claim_incident


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
) -> dict[str, Any]:
    """Persist a versioned C4 result and its complete trail.

    When no version is supplied, the next integer version is allocated in the
    same transaction as the result insert. Persisting a result also completes
    the incident lifecycle for a runner that claimed it.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if not isinstance(result, Mapping):
        raise TypeError("result must be a mapping")

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
        connection.execute(
            "UPDATE incident SET lifecycle_state = 'diagnosed' "
            "WHERE incident_id = ? AND lifecycle_state IN ('detected', 'investigating')",
            (incident_id,),
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


def _epoch(value: Any) -> int:
    if not isinstance(value, str) or not value:
        return 0
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0
