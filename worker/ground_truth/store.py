"""Quarantined store for C6 (docs/contracts/hidden-truth.md).

Written by W1 only, while a scenario is running (worker/ground_truth/runner.py).
Read only by the evaluator, only after a diagnosis exists.

The quarantine is a real process and storage boundary, not a naming
convention: this module lives under worker/, is never imported by
detector/ or investigation/, and the SQLite file it writes is never
mounted into a detector or investigation container. The evaluator is the
only other reader. In a containerised run each merchant worker bind-mounts
its own file at state/ground_truth/<merchant_id>/ground_truth.db so the
evaluator can score from the host after the window closes; CLEARWAVE_GROUND_TRUTH_DB
overrides the path inside the worker. W2/W3: if you are reading this file
to figure out how to get at hidden truth, stop - that is exactly the read
path this document exists to deny you. See docs/contracts/hidden-truth.md
for why.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).parent / "state" / "ground_truth.db"
DB_ENV_VAR = "CLEARWAVE_GROUND_TRUTH_DB"


def resolve_db_path(path: Path | str | None = None) -> Path | str:
    if path is not None:
        return path
    override = os.environ.get(DB_ENV_VAR)
    if override:
        return override
    return DEFAULT_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS hidden_truth (
    instance_id     TEXT PRIMARY KEY,
    scenario_id     TEXT NOT NULL,
    scenario_name   TEXT NOT NULL,
    injected        TEXT NOT NULL,
    observed        TEXT,
    evaluation      TEXT,
    created_at      TEXT NOT NULL,
    closed_at       TEXT
);
CREATE INDEX IF NOT EXISTS hidden_truth_scenario ON hidden_truth (scenario_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect(path: Path | str | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    target = resolve_db_path(path)
    if readonly:
        db = Path(target).resolve()
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    if str(target) != ":memory:":
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def record_injection(
    connection: sqlite3.Connection,
    *,
    scenario_id: str,
    scenario_name: str,
    affected_cohort: dict[str, str],
    failure_mode: str,
    strength: dict[str, Any],
    start_time: str,
    end_time: str,
    event_time_bucket_seconds: int,
    instance_id: str | None = None,
) -> str:
    """Record the injected configuration. Returns the instance_id to close() later."""
    instance_id = instance_id or f"{scenario_id}-{uuid.uuid4().hex[:8]}"
    injected = {
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "affected_cohort": affected_cohort,
        "failure_mode": failure_mode,
        "strength": strength,
        "start_time": start_time,
        "end_time": end_time,
        "event_time_bucket_seconds": event_time_bucket_seconds,
    }
    connection.execute(
        "INSERT INTO hidden_truth (instance_id, scenario_id, scenario_name, injected, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instance_id, scenario_id, scenario_name, json.dumps(injected), _now_iso()),
    )
    connection.commit()
    return instance_id


def record_observation(
    connection: sqlite3.Connection,
    instance_id: str,
    observed: dict[str, Any],
    evaluation: dict[str, Any],
) -> None:
    """Extend an injection with what the replay actually produced, once the window closes."""
    connection.execute(
        "UPDATE hidden_truth SET observed = ?, evaluation = ?, closed_at = ? WHERE instance_id = ?",
        (json.dumps(observed), json.dumps(evaluation), _now_iso(), instance_id),
    )
    connection.commit()


def read_hidden_truth(connection: sqlite3.Connection, instance_id: str) -> dict[str, Any] | None:
    """The evaluator's read path - see docs/contracts/hidden-truth.md. Not for W2/W3."""
    row = connection.execute(
        "SELECT * FROM hidden_truth WHERE instance_id = ?", (instance_id,)
    ).fetchone()
    if row is None:
        return None
    injected = json.loads(row["injected"])
    result: dict[str, Any] = {
        "scenario_id": row["scenario_id"],
        "scenario_name": row["scenario_name"],
        "injected": injected,
    }
    if row["observed"]:
        result["observed"] = json.loads(row["observed"])
    if row["evaluation"]:
        result["evaluation"] = json.loads(row["evaluation"])
    return result


def list_hidden_truth(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Instance ids and closed-state for every record in this store. Evaluator only."""
    rows = connection.execute(
        "SELECT instance_id, scenario_id, scenario_name, observed, created_at, closed_at "
        "FROM hidden_truth ORDER BY created_at"
    ).fetchall()
    return [
        {
            "instance_id": row["instance_id"],
            "scenario_id": row["scenario_id"],
            "scenario_name": row["scenario_name"],
            "created_at": row["created_at"],
            "closed_at": row["closed_at"],
            "closed": row["closed_at"] is not None and row["observed"] is not None,
        }
        for row in rows
    ]
