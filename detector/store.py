"""SQLite persistence for the detection plane.

Five tables, each earning its place:

* ``attempt`` holds raw normalised rows. Drill-down is a GROUP BY over this,
  which is what lets us localise to a cohort nobody pre-declared.
* ``telemetry_sample`` holds W1's per-service gauges, the only first-party
  source of runtime health; the attempt stream carries none.
* ``payment_closed`` holds observed terminality, so "did this payment end, and
  how" never has to be guessed from a quiet window.
* ``incident`` holds C3 records and their lifecycle.
* ``dead_letter`` holds what we refused, with the reason and where it came from.

Every one of the three event tables is keyed on ``event_id``, which is what
turns at-least-once delivery into exactly-once counting.

Relational and embedded on purpose: one file, no daemon, no container between
us and a working demo, and the file itself is evidence a judge can be handed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from . import mappers, schema

DEFAULT_DB = Path("state/clearwave.db")

# One environment variable locates the store for every W2 entry point: the CLI
# and each C2 evidence tool. Without it the default is a repository-relative
# path, so a tool invoked from the repository root works with no configuration
# at all - which is what CI does.
DB_ENV_VAR = "CLEARWAVE_DB"

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempt (
    event_id                  TEXT PRIMARY KEY,
    payment_id                TEXT NOT NULL,
    attempt_id                TEXT NOT NULL,
    attempt_number            INTEGER NOT NULL,
    occurred_at               TEXT NOT NULL,
    occurred_epoch            INTEGER NOT NULL,
    merchant_id               TEXT NOT NULL,
    provider                  TEXT NOT NULL,
    payment_method            TEXT NOT NULL,
    card_network              TEXT,
    country                   TEXT NOT NULL,
    issuing_bank              TEXT,
    status                    TEXT NOT NULL,
    normalized_decline_reason TEXT,
    provider_raw_code         TEXT,
    amount_usd                REAL NOT NULL,
    currency                  TEXT NOT NULL,
    latency_ms                REAL,
    queue_depth               REAL,
    queue_delay_ms            REAL,
    deployment_id             TEXT,
    service_id                TEXT
);
CREATE INDEX IF NOT EXISTS attempt_time ON attempt (occurred_epoch);
CREATE INDEX IF NOT EXISTS attempt_payment ON attempt (payment_id, attempt_number);
CREATE INDEX IF NOT EXISTS attempt_cohort ON attempt (provider, country, issuing_bank);

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

CREATE TABLE IF NOT EXISTS dead_letter (
    rowid_alias INTEGER PRIMARY KEY AUTOINCREMENT,
    reason      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'ingest'
);

CREATE TABLE IF NOT EXISTS telemetry_sample (
    event_id           TEXT PRIMARY KEY,
    sample_at          TEXT NOT NULL,
    sample_epoch       INTEGER NOT NULL,
    service_id         TEXT NOT NULL,
    deployment_id      TEXT,
    healthy            INTEGER,
    queue_depth        REAL,
    queue_delay_p95_ms REAL,
    cpu_pct            REAL,
    error_rate         REAL,
    restarts_total     REAL
);
CREATE INDEX IF NOT EXISTS telemetry_service_time
    ON telemetry_sample (service_id, sample_epoch);

CREATE TABLE IF NOT EXISTS payment_closed (
    event_id         TEXT PRIMARY KEY,
    payment_id       TEXT NOT NULL,
    closed_at        TEXT NOT NULL,
    closed_epoch     INTEGER NOT NULL,
    outcome          TEXT NOT NULL,
    final_attempt_id TEXT,
    total_attempts   INTEGER NOT NULL,
    merchant_id      TEXT NOT NULL,
    country          TEXT,
    payment_method   TEXT,
    amount_usd       REAL NOT NULL,
    currency         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS closed_time ON payment_closed (closed_epoch);
"""

# One record kind per topic W1 publishes. The kind decides which normaliser and
# which table a record reaches; nothing else in the pipeline branches on topic.
# The lifecycle state a near-miss is persisted under. It is a state on the C3
# record rather than a separate store (DECISIONS.md, 2026-08-30T03:59Z): one
# cohort keeps one record, so the warning and the incident it becomes are the
# same row. `detected` remains the sole handoff signal to investigation, and a
# watch is never claimed and never escalated.
WATCHING = "watching"
RESOLVED = "resolved"

# The states in which a record still describes something happening now, which
# is the set a later sweep may re-measure and a later reading may adopt. It is
# the C3 lifecycle vocabulary (docs/contracts/incident.md) plus `diagnosed`,
# which investigation sets, minus the two states that mean the episode is over:
# `resolved`, and `mitigated` - the pair `surfaces/present.py` already calls
# INACTIVE_STATES. A closed episode is never rewritten and never adopted, so a
# fault that returns after it is a second incident rather than a resurrection
# of the first, on a row nobody is looking at any more.
LIVE_STATES = (
    WATCHING,
    "detected",
    "investigating",
    "diagnosed",
    "acknowledged",
)

KINDS = {
    "attempt": "attempt",
    "telemetry": "telemetry_sample",
    "closed": "payment_closed",
}


def database_path() -> Path:
    """Where the store lives: ``CLEARWAVE_DB`` if set, else the default path."""
    return Path(os.environ.get(DB_ENV_VAR) or DEFAULT_DB)


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Open the store, creating it and its parent directory if needed."""
    target = Path(path)
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(SCHEMA)
    _add_missing_columns(connection)
    return connection


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """Bring a store created by an earlier build up to the current shape.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table alone, so a column
    added later has to be added explicitly. The demo store is a checked-in file
    and deleting it to pick up a schema change is exactly the sort of step that
    gets skipped at 03:00.
    """
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(dead_letter)").fetchall()
    }
    if existing and "source" not in existing:
        connection.execute(
            "ALTER TABLE dead_letter ADD COLUMN source TEXT NOT NULL DEFAULT 'ingest'"
        )
        connection.commit()


def normalise_record(raw: Any, kind: str = "attempt") -> dict[str, Any]:
    """Normalise one record of a given kind, raising on anything untrustworthy.

    Attempts go through the mapper registry and C1b exactly as they always have.
    There is one normalisation path per record kind and no second one anywhere:
    the file loader and the Kafka consumer both arrive here.
    """
    if kind == "attempt":
        return schema.normalise(mappers.to_canonical(raw))
    if kind == "telemetry":
        return schema.normalise_telemetry(raw)
    if kind == "closed":
        return schema.normalise_closed(raw)
    if kind == "unroutable":
        # Already known bad on arrival - an undecodable payload or a topic we do
        # not consume. It still reaches the dead-letter table with its reason,
        # because a message dropped without trace is indistinguishable from one
        # that never arrived.
        raise schema.InvalidEvent(str((raw or {}).get("reason") or "unroutable record"))
    raise mappers.UnknownShape(f"no normaliser registered for record kind {kind!r}")


def write_batch(
    connection: sqlite3.Connection,
    records: Iterable[tuple[str, Any]],
    source: str = "ingest",
) -> dict[str, int]:
    """Stage one batch of ``(kind, raw)`` records inside the caller's transaction.

    Deliberately does **not** commit. The consumer needs the store write to be
    durable strictly before it advances a Kafka offset, and it can only order
    those two if it owns the commit.

    Duplicates are ignored rather than counted twice: `event_id` is the primary
    key, so ``INSERT OR IGNORE`` is what turns at-least-once delivery into
    exactly-once counting, and `rowcount` tells us which of the two happened.
    """
    counts = {"accepted": 0, "duplicates": 0, "rejected": 0}
    for kind, raw in records:
        try:
            row = normalise_record(raw, kind)
            table = KINDS[kind]
        except (schema.InvalidEvent, mappers.UnknownShape, KeyError) as exc:
            connection.execute(
                "INSERT INTO dead_letter (reason, payload, source) VALUES (?, ?, ?)",
                (str(exc), json.dumps(raw, sort_keys=True, default=str), source),
            )
            counts["rejected"] += 1
            continue
        columns = sorted(row)
        placeholders = ", ".join("?" for _ in columns)
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )
        counts["duplicates" if cursor.rowcount == 0 else "accepted"] += 1
    return counts


def stored_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """How many rows of each kind the store holds. The honest total, post-dedupe."""
    return {
        kind: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for kind, table in KINDS.items()
    }


def ingest(connection: sqlite3.Connection, events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Normalise and store canonical attempt events from a file or generator.

    The demo's offline path. It stays independent of Kafka on purpose: if the
    broker will not come up on the morning, `seed`, `ingest` and `detect` are
    still a complete demonstration.
    """
    counts = write_batch(connection, (("attempt", event) for event in events))
    connection.commit()
    stored = connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"]
    return {
        "accepted": counts["accepted"] + counts["duplicates"],
        "duplicates": counts["duplicates"],
        "rejected": counts["rejected"],
        "stored": stored,
    }


def ingest_stream(
    connection: sqlite3.Connection,
    records: Iterable[tuple[str, Any]],
    batch_size: int = 1000,
    source: str = "ingest",
) -> dict[str, int]:
    """Same summary as `ingest`, but never holds the whole source in memory.

    `ingest` takes a list, which is right for the fixture files that fit in a
    breath. A 15-day backfill does not: 83 MB of text plus 100k parsed dicts is
    a working set nobody needs, since every record is finished with the moment
    it is written.

    This drains any iterable of ``(kind, raw)`` in batches, reusing
    `write_batch` - the consumer's own batching - so there is exactly one
    insert path, one dead-letter path and one dedupe rule in the codebase
    rather than two that drift. Taking `(kind, raw)` rather than bare events is
    what lets a reader hand over a line it could not parse as ``unroutable``,
    so a malformed line in a 100k-line file is dead-lettered with its reason
    instead of aborting the whole load.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    counts = {"accepted": 0, "duplicates": 0, "rejected": 0}
    batch: list[tuple[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        written = write_batch(connection, batch, source=source)
        connection.commit()
        for key in counts:
            counts[key] += written[key]
        batch.clear()

    for record in records:
        batch.append(record)
        if len(batch) >= batch_size:
            flush()
    flush()

    stored = connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"]
    return {
        "accepted": counts["accepted"] + counts["duplicates"],
        "duplicates": counts["duplicates"],
        "rejected": counts["rejected"],
        "stored": stored,
    }


def window_bounds(connection: sqlite3.Connection) -> tuple[int, int] | None:
    """Return the epoch range across all stored event kinds, or None if empty."""
    row = connection.execute(
        """
        SELECT MIN(event_epoch) AS lo, MAX(event_epoch) AS hi
        FROM (
            SELECT occurred_epoch AS event_epoch FROM attempt
            UNION ALL
            SELECT sample_epoch AS event_epoch FROM telemetry_sample
            UNION ALL
            SELECT closed_epoch AS event_epoch FROM payment_closed
        )
        """
    ).fetchone()
    if row is None or row["lo"] is None:
        return None
    return int(row["lo"]), int(row["hi"])


def dimension_values(connection: sqlite3.Connection, dimension: str) -> list[str]:
    """Distinct non-null values of one dimension, ordered for determinism."""
    if dimension not in schema.DIMENSIONS:
        raise ValueError(f"{dimension!r} is not a cohort dimension")
    rows = connection.execute(
        f"SELECT DISTINCT {dimension} AS value FROM attempt "
        f"WHERE {dimension} IS NOT NULL ORDER BY {dimension}"
    ).fetchall()
    return [row["value"] for row in rows]


def save_incident(
    connection: sqlite3.Connection,
    incident: dict[str, Any],
    lifecycle_state: str = "detected",
) -> bool:
    """Durably record one C3 record. Returns False when the write changed nothing.

    One cohort keeps one record (DECISIONS.md, 2026-08-30T03:59Z), so a later
    sweep of the same episode is a re-measurement of a row that already exists,
    not a second row. What it may rewrite depends on what it is writing.

    A *measurement* - severity, severity score, the C3 record itself, the
    cohort it localises to, onset and `last_seen_epoch` - may always be
    rewritten while the episode is live. An incident that gets worse has to be
    able to say so on the row a judge is already looking at, and one that
    recovers has to be able to fall back down again; a store that can only ever
    show the first reading is a board that lies as soon as the fault moves.

    ``lifecycle_state`` is the one field this write does **not** own. It is the
    handoff signal to investigation (DECISIONS.md, 2026-08-29T19:43Z, and
    2026-08-29T20:32Z), so:

    * a row still ``watching`` is upgraded to ``detected`` when the floors pass
      - that upgrade is the whole point of keeping the warning and the incident
      on one identifier - and a watch write keeps it ``watching``;
    * a row that has left ``watching`` - investigating, diagnosed, acknowledged
      - keeps exactly the state its owner left it in, however far its numbers
      move. Being re-measured is not being re-opened;
    * a watch write can never pull a row that has left ``watching`` back into
      it, so the ``watching`` rules stay precisely as they were;
    * a row outside ``LIVE_STATES`` - ``resolved`` or ``mitigated`` - is a
      closed episode and is never rewritten at all.
    """
    record = dict(incident)
    detection = record.get("detection") or {}
    onset = schema.parse_timestamp(record["onset"])
    last_seen_at = (record.get("persistence") or {}).get("last_observed_at")
    last_seen = schema.parse_timestamp(last_seen_at) if last_seen_at else onset
    incident_id = str(record["incident_id"])

    def measurement(state: str) -> tuple[Any, ...]:
        stored = dict(record)
        stored["lifecycle_state"] = state
        return (
            json.dumps(stored, sort_keys=True, default=str),
            _cohort_key(stored.get("affected_cohort") or {}),
            str(stored.get("severity", "low")),
            float(detection.get("severity_score") or 0.0),
            state,
            int(onset.timestamp()),
            int(last_seen.timestamp()),
            str(detection.get("config_version") or "unknown"),
        )

    with connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO incident
               (incident_id, created_at, record, cohort_key, severity, severity_score,
                lifecycle_state, onset_epoch, last_seen_epoch, config_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (incident_id, record["onset"]) + measurement(lifecycle_state),
        )
        if cursor.rowcount == 1:
            return True
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - deleted between the two statements
            return False
        current = str(row["lifecycle_state"])
        if lifecycle_state == WATCHING:
            # Unchanged from before: only a row still watching may be written
            # as a watch, so nothing that has moved on is ever pulled back.
            if current != WATCHING:
                return False
            state = WATCHING
        else:
            if current not in LIVE_STATES:
                return False
            # The watching -> detected upgrade is ours. Every other state
            # belongs to whoever set it.
            state = lifecycle_state if current == WATCHING else current
        cursor = connection.execute(
            """UPDATE incident
                  SET record = ?, cohort_key = ?, severity = ?, severity_score = ?,
                      lifecycle_state = ?, onset_epoch = ?, last_seen_epoch = ?,
                      config_version = ?
                WHERE incident_id = ? AND lifecycle_state = ?""",
            measurement(state) + (incident_id, current),
        )
    return cursor.rowcount == 1


def _cohort_key(cohort: dict[str, Any]) -> str:
    """Local copy of the readable cohort identity, to keep store import-light."""
    if not cohort:
        return "*"
    return "|".join(f"{key}={cohort[key]}" for key in sorted(cohort))


def load_incident(connection: sqlite3.Connection, incident_id: str) -> dict[str, Any] | None:
    """Return one stored C3 record, or None when it is not in the store."""
    row = connection.execute(
        "SELECT record, lifecycle_state FROM incident WHERE incident_id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        return None
    return _record_of(row)


def list_incidents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every stored C3 record, most recent onset first. Deterministic order."""
    rows = connection.execute(
        "SELECT record, lifecycle_state FROM incident "
        "ORDER BY onset_epoch DESC, incident_id ASC"
    ).fetchall()
    return [record for record in (_record_of(row) for row in rows) if record is not None]


def live_incidents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """C3 records for episodes still open, oldest onset first.

    Open means anything in `LIVE_STATES`: a `resolved` row is a closed episode
    and is deliberately absent, so nothing here can resurrect one. Ordered by
    onset so a caller adopting an identity always adopts the earliest, and the
    row a judge is already looking at is the row that keeps moving.
    """
    placeholders = ", ".join("?" for _ in LIVE_STATES)
    rows = connection.execute(
        f"SELECT record, lifecycle_state FROM incident "
        f"WHERE lifecycle_state IN ({placeholders}) "
        f"ORDER BY onset_epoch ASC, incident_id ASC",
        LIVE_STATES,
    ).fetchall()
    return [record for record in (_record_of(row) for row in rows) if record is not None]


def expire_watches_except(connection: sqlite3.Connection, keep_ids: set[str]) -> int:
    """Move watching rows this sweep no longer wants to resolved.

    A watch is a claim about the present. Once the present no longer supports
    it, leaving the row in `watching` floods the board with warnings that are
    no longer true. The existing save guard still holds: only a row that is
    still watching can be expired this way, so a claimed or diagnosed incident
    is never touched by this path - recovered incidents use
    `resolve_recovered_incidents` instead.
    """
    rows = connection.execute(
        "SELECT incident_id, record FROM incident WHERE lifecycle_state = ?",
        (WATCHING,),
    ).fetchall()
    expired = 0
    with connection:
        for row in rows:
            if row["incident_id"] in keep_ids:
                continue
            try:
                record = json.loads(row["record"])
            except (TypeError, ValueError):
                record = {}
            if not isinstance(record, dict):
                record = {}
            record["lifecycle_state"] = RESOLVED
            cursor = connection.execute(
                """UPDATE incident SET record = ?, lifecycle_state = ?
                     WHERE incident_id = ? AND lifecycle_state = ?""",
                (
                    json.dumps(record, sort_keys=True, default=str),
                    RESOLVED,
                    row["incident_id"],
                    WATCHING,
                ),
            )
            expired += cursor.rowcount
    return expired


# States that mean "still an open problem". When traffic recovers, these must
# leave the board - otherwise Clear never returns the demo to healthy.
RECOVERABLE_STATES = frozenset(
    {
        "detected",
        "investigating",
        "diagnosed",
        "acknowledged",
    }
)


def resolve_recovered_incidents(
    connection: sqlite3.Connection,
    recovered_ids: set[str],
) -> int:
    """Move open incident rows whose traffic has recovered to resolved.

    Watches already expire through `expire_watches_except`. Detected and
    diagnosed rows did not: after the judge presses Clear the inject stops,
    conversion recovers, and the board still showed the old money. Only the
    ids the sweep has measured as recovered are touched, and only while they
    still sit in a recoverable state - a mitigated row is already closed.
    """
    if not recovered_ids:
        return 0
    resolved = 0
    with connection:
        for incident_id in sorted(recovered_ids):
            row = connection.execute(
                "SELECT record, lifecycle_state FROM incident WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                continue
            state = str(row["lifecycle_state"] or "")
            if state not in RECOVERABLE_STATES:
                continue
            try:
                record = json.loads(row["record"])
            except (TypeError, ValueError):
                record = {}
            if not isinstance(record, dict):
                record = {}
            record["lifecycle_state"] = RESOLVED
            cursor = connection.execute(
                """UPDATE incident SET record = ?, lifecycle_state = ?
                     WHERE incident_id = ? AND lifecycle_state = ?""",
                (
                    json.dumps(record, sort_keys=True, default=str),
                    RESOLVED,
                    incident_id,
                    state,
                ),
            )
            resolved += cursor.rowcount
    return resolved


def _record_of(row: sqlite3.Row) -> dict[str, Any] | None:
    """Decode a stored record, tolerating a row another writer shaped."""
    try:
        record = json.loads(row["record"])
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    record["lifecycle_state"] = row["lifecycle_state"]
    return record
