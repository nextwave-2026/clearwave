"""Read the shared W2/W3 SQLite store and persist W4 channel outcomes.

W4 does not invent a second read model for incidents. It reads the C3 record
and the C4 result that W2 and W3 already wrote, and it records only its own
escalation outcomes in the same file.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from investigation.store import connect as open_investigation_store
from investigation.store import read_result

from .escalation import escalate

DEFAULT_DB = Path("state/clearwave.db")

# Stored severity is already the business priority. This rank is only a sort
# key over that stored label; it does not compute or adjust severity.
SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

ESCALATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalation_event (
    incident_id TEXT NOT NULL,
    channel     TEXT NOT NULL,
    status      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (incident_id, channel)
);
CREATE TABLE IF NOT EXISTS pending_call (
    incident_id TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Open the shared store and ensure W4's outcome tables exist."""
    connection = open_investigation_store(path)
    connection.executescript(ESCALATION_SCHEMA)
    return connection


@contextmanager
def session(path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
    finally:
        connection.close()


def list_incidents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every C3 record, ordered by stored business priority, never by recency."""
    rows = connection.execute(
        "SELECT incident_id, record, lifecycle_state, severity FROM incident"
    ).fetchall()
    incidents = []
    for row in rows:
        record = _decode_record(row)
        if record is not None:
            incidents.append(record)
    incidents.sort(key=_priority_key)
    return incidents


def load_incident(connection: sqlite3.Connection, incident_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT incident_id, record, lifecycle_state, severity FROM incident "
        "WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    if row is None:
        return None
    return _decode_record(row)


def load_investigation(connection: sqlite3.Connection, incident_id: str) -> dict[str, Any] | None:
    """Latest C4 wrapper, including the evidence trail, or None."""
    return read_result(connection, incident_id)


def load_escalation(connection: sqlite3.Connection, incident_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT channel, status, payload, detail, created_at FROM escalation_event "
        "WHERE incident_id = ? ORDER BY channel ASC",
        (incident_id,),
    ).fetchall()
    events = []
    for row in rows:
        events.append(
            {
                "channel": row["channel"],
                "status": row["status"],
                "payload": json.loads(row["payload"]),
                "detail": row["detail"],
                "created_at": row["created_at"],
            }
        )
    return events


def ensure_escalation(
    connection: sqlite3.Connection,
    incident: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Fire channels once per incident. Repeat reads return the recorded outcomes."""
    incident_id = str(incident.get("incident_id", ""))
    existing = load_escalation(connection, incident_id)
    if existing:
        return existing

    def enqueue_call(call_id: str, payload: Mapping[str, Any]) -> None:
        record_pending_call(connection, call_id, payload)

    outcomes = escalate(
        incident,
        result,
        enqueue_call=enqueue_call,
        **kwargs,
    )
    now = _utc_now()
    with connection:
        for outcome in outcomes:
            connection.execute(
                """INSERT OR IGNORE INTO escalation_event
                   (incident_id, channel, status, payload, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    str(outcome.get("channel", "")),
                    str(outcome.get("status", "attempted")),
                    json.dumps(outcome.get("payload", {}), sort_keys=True, default=str),
                    outcome.get("detail"),
                    now,
                ),
            )
    return load_escalation(connection, incident_id) or [
        {key: value for key, value in outcome.items() if key != "payload"} | {
            "payload": dict(outcome.get("payload") or {}),
            "created_at": now,
        }
        for outcome in outcomes
    ]


def record_pending_call(
    connection: sqlite3.Connection,
    incident_id: str,
    payload: Mapping[str, Any],
) -> None:
    with connection:
        connection.execute(
            """INSERT OR IGNORE INTO pending_call
               (incident_id, payload, created_at, acknowledged)
               VALUES (?, ?, ?, 0)""",
            (incident_id, json.dumps(payload, sort_keys=True, default=str), _utc_now()),
        )


def list_pending_calls(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT incident_id, payload, created_at FROM pending_call "
        "WHERE acknowledged = 0 ORDER BY created_at ASC, incident_id ASC"
    ).fetchall()
    calls = []
    for row in rows:
        calls.append(
            {
                "incident_id": row["incident_id"],
                "payload": json.loads(row["payload"]),
                "created_at": row["created_at"],
            }
        )
    return calls


def acknowledge_call(connection: sqlite3.Connection, incident_id: str) -> bool:
    with connection:
        cursor = connection.execute(
            "UPDATE pending_call SET acknowledged = 1 "
            "WHERE incident_id = ? AND acknowledged = 0",
            (incident_id,),
        )
    return cursor.rowcount == 1


def _decode_record(row: sqlite3.Row) -> dict[str, Any] | None:
    try:
        record = json.loads(row["record"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    record["lifecycle_state"] = row["lifecycle_state"]
    if not record.get("severity"):
        record["severity"] = row["severity"]
    return record


def _priority_key(incident: Mapping[str, Any]) -> tuple[int, str]:
    severity = str(incident.get("severity", "")).lower()
    return (SEVERITY_RANK.get(severity, 99), str(incident.get("incident_id", "")))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
