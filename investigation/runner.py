"""Polling runner for watches and detected incidents."""

from __future__ import annotations

import inspect
import json
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .agent import InvestigationAgent, InvestigationRun
from .contracts import InvestigationResult
from .degrade import degrade_result
from .gateway import EvidenceGateway
from .store import (
    CLAIMABLE_STATES,
    append_trail_entry,
    claim_incident,
    connect as open_store,
    evidence_fingerprint,
    next_result_version,
    persist_result,
    prepare,
    read_bound_fingerprint,
    reclaim_expired_claims,
)
from .trail import EvidenceTrail

_CLAIMABLE_SQL = "lifecycle_state IN ('detected', 'watching')"


class InvestigationRunner:
    """Claim watches and detected incidents and process them with bounded concurrency."""

    def __init__(
        self,
        connection: Any,
        agent: InvestigationAgent | Any | None = None,
        *,
        max_concurrency: int = 1,
        poll_interval_seconds: float = 0.25,
        incident_ids: Sequence[str] | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.connection = connection
        self.agent = agent or InvestigationAgent()
        self.max_concurrency = int(max_concurrency)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.incident_ids = tuple(str(value) for value in incident_ids) if incident_ids else None
        self.model_calls = 0
        prepare(self.connection)
        self._db_path = _database_file(connection)
        self._result_versions: dict[str, int] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrency)
        self._futures: set[Future[InvestigationRun]] = set()
        self._lock = threading.Lock()

    def close(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    def poll_once(self, *, wait: bool = True) -> list[InvestigationRun]:
        """Claim up to the available slots and optionally wait for completion."""
        finished = self._collect_finished(persist=True)
        slots = self.max_concurrency - len(self._futures)
        if slots <= 0:
            return finished
        claimed = self._claim_pending(slots)
        for incident in claimed:
            incident_id = str(incident["incident_id"])
            with self._lock:
                self._result_versions[incident_id] = next_result_version(
                    self.connection, incident_id
                )
            future = self._executor.submit(self._investigate, incident)
            self._futures.add(future)
        if not wait:
            return finished
        results: list[InvestigationRun] = list(finished)
        for future in list(self._futures):
            results.append(future.result())
            self._futures.remove(future)
            self._persist(results[-1])
        return results

    run_once = poll_once
    poll = poll_once

    def run_forever(
        self,
        stop_event: threading.Event | None = None,
        *,
        max_polls: int | None = None,
    ) -> None:
        """Poll until stopped, while model work runs in bounded workers."""
        event = stop_event or threading.Event()
        polls = 0
        try:
            while not event.is_set() and (max_polls is None or polls < max_polls):
                self.poll_once(wait=False)
                self._collect_finished(persist=True)
                polls += 1
                if not event.wait(self.poll_interval_seconds):
                    continue
            while self._futures:
                self._collect_finished(persist=True)
                if self._futures:
                    event.wait(self.poll_interval_seconds)
        finally:
            self._collect_finished(persist=True)

    start = run_forever

    def _claim_pending(self, limit: int) -> list[dict[str, Any]]:
        reclaim_expired_claims(self.connection)
        rows = self._pending_rows()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            if len(claimed) >= limit:
                break
            incident_id = str(row["incident_id"])
            record = json.loads(row["record"])
            if not isinstance(record, Mapping):
                continue
            record = dict(record)
            record["lifecycle_state"] = str(row["lifecycle_state"])
            if self.incident_ids is None:
                current = evidence_fingerprint(record)
                previous = read_bound_fingerprint(self.connection, incident_id)
                if previous is not None and previous == current:
                    continue
            if not claim_incident(self.connection, incident_id):
                continue
            claimed.append(record)
        return claimed

    def _pending_rows(self) -> list[Any]:
        order = (
            "ORDER BY CASE lifecycle_state WHEN 'detected' THEN 0 ELSE 1 END, "
            "created_at, incident_id"
        )
        # Watches keep lifecycle_state=watching while claimed, so exclude rows
        # that already hold a claim lease or the runner would re-queue them.
        unclaimed = (
            f"WHERE {_CLAIMABLE_SQL} AND incident_id NOT IN "
            "(SELECT incident_id FROM investigation_claim)"
        )
        if self.incident_ids is None:
            return self.connection.execute(
                "SELECT incident_id, record, lifecycle_state FROM incident "
                f"{unclaimed} {order}"
            ).fetchall()
        placeholders = ",".join("?" for _ in self.incident_ids)
        return self.connection.execute(
            "SELECT incident_id, record, lifecycle_state FROM incident "
            f"{unclaimed} AND incident_id IN ({placeholders}) {order}",
            self.incident_ids,
        ).fetchall()

    def _investigate(self, incident: Mapping[str, Any]) -> InvestigationRun:
        started = time.monotonic()
        started_at = _utc_now()
        incident_id = str(incident.get("incident_id") or "")
        with self._lock:
            version = self._result_versions.get(incident_id)
        live = self._connect_live()
        persist_entry = None
        if live is not None and incident_id and version is not None:
            persist_entry = _live_persist(live, incident_id, version)
        gateway = EvidenceGateway(
            query_budget=getattr(self.agent, "query_budget", 6),
            persist_entry=persist_entry,
        )
        try:
            output = self._invoke_agent(incident, gateway)
            if isinstance(output, InvestigationRun):
                return self._stamp(output, incident)
            if isinstance(output, InvestigationResult):
                result = output
            elif isinstance(output, Mapping):
                result = InvestigationResult.model_validate(output)
            else:
                raise TypeError("agent must return an InvestigationRun or C4 result mapping")
            return self._stamp(
                InvestigationRun(
                    result=result,
                    trail=EvidenceTrail(),
                    started_at=started_at,
                    completed_at=_utc_now(),
                    duration_ms=round((time.monotonic() - started) * 1000.0, 3),
                ),
                incident,
            )
        except (ValidationError, TypeError, ValueError, RuntimeError, OSError) as exc:
            result = degrade_result(incident, reason=f"runner recovered from agent failure: {exc}")
            return self._stamp(
                InvestigationRun(
                    result=result,
                    trail=EvidenceTrail(),
                    started_at=started_at,
                    completed_at=_utc_now(),
                    duration_ms=round((time.monotonic() - started) * 1000.0, 3),
                ),
                incident,
            )
        finally:
            if live is not None:
                live.close()

    def _stamp(self, run: InvestigationRun, incident: Mapping[str, Any]) -> InvestigationRun:
        claimed_from = str(incident.get("lifecycle_state") or "detected")
        if claimed_from not in CLAIMABLE_STATES:
            claimed_from = "detected"
        run.claimed_from = claimed_from
        run.evidence_fingerprint = evidence_fingerprint(incident)
        return run

    def _invoke_agent(self, incident: Mapping[str, Any], gateway: EvidenceGateway) -> Any:
        method = self.agent.investigate
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "gateway" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            return method(incident, gateway=gateway)
        return method(incident)

    def _connect_live(self) -> Any | None:
        if not self._db_path:
            return None
        try:
            return open_store(self._db_path)
        except Exception:
            return None

    def _persist(self, run: InvestigationRun) -> None:
        incident_id = run.result.incident_id
        with self._lock:
            version = self._result_versions.pop(incident_id, None)
        persist_result(
            self.connection,
            incident_id,
            run.result_dict,
            run.result.outcome,
            version=version,
            trail=run.trail,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_ms=run.duration_ms,
            resume_state=run.claimed_from,
            evidence_fingerprint=run.evidence_fingerprint or None,
        )
        self.model_calls += 1
        print(
            f"investigation persisted {run.result.incident_id} "
            f"from={run.claimed_from} outcome={run.result.outcome} "
            f"model_calls_this_process={self.model_calls}",
            flush=True,
        )

    def _collect_finished(self, *, persist: bool = False) -> list[InvestigationRun]:
        finished: list[InvestigationRun] = []
        for future in list(self._futures):
            if not future.done():
                continue
            self._futures.remove(future)
            run = future.result()
            if persist:
                self._persist(run)
            finished.append(run)
        return finished


def _live_persist(connection: Any, incident_id: str, version: int):
    def persist_entry(entry: Mapping[str, Any]) -> None:
        append_trail_entry(connection, incident_id, entry, version=version)

    return persist_entry


def _database_file(connection: Any) -> str:
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except Exception:
        return ""
    for row in rows:
        try:
            name = row[1]
            path = row[2]
        except (IndexError, TypeError, KeyError):
            continue
        if str(name) == "main":
            return str(path or "")
    return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["InvestigationRunner"]
