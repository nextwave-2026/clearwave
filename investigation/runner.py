"""Polling runner for detected incidents."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .agent import InvestigationAgent, InvestigationRun
from .contracts import InvestigationResult
from .degrade import degrade_result
from .store import persist_result
from .trail import EvidenceTrail


class InvestigationRunner:
    """Claim detected incidents and process them with bounded concurrency."""

    def __init__(
        self,
        connection: Any,
        agent: InvestigationAgent | Any | None = None,
        *,
        max_concurrency: int = 1,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.connection = connection
        self.agent = agent or InvestigationAgent()
        self.max_concurrency = int(max_concurrency)
        self.poll_interval_seconds = float(poll_interval_seconds)
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
        claimed = self._claim_detected(slots)
        for incident in claimed:
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

    def _claim_detected(self, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT incident_id, record FROM incident "
            "WHERE lifecycle_state = 'detected' ORDER BY created_at, incident_id LIMIT ?",
            (limit,),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for row in rows:
            incident_id = str(row["incident_id"])
            from .store import claim_incident

            if not claim_incident(self.connection, incident_id):
                continue
            record = json.loads(row["record"])
            if isinstance(record, Mapping):
                claimed.append(dict(record))
        return claimed

    def _investigate(self, incident: Mapping[str, Any]) -> InvestigationRun:
        started = time.monotonic()
        started_at = _utc_now()
        try:
            output = self.agent.investigate(incident)
            if isinstance(output, InvestigationRun):
                return output
            if isinstance(output, InvestigationResult):
                result = output
            elif isinstance(output, Mapping):
                result = InvestigationResult.model_validate(output)
            else:
                raise TypeError("agent must return an InvestigationRun or C4 result mapping")
            return InvestigationRun(
                result=result,
                trail=EvidenceTrail(),
                started_at=started_at,
                completed_at=_utc_now(),
                duration_ms=round((time.monotonic() - started) * 1000.0, 3),
            )
        except (ValidationError, TypeError, ValueError, RuntimeError, OSError) as exc:
            result = degrade_result(incident, reason=f"runner recovered from agent failure: {exc}")
            return InvestigationRun(
                result=result,
                trail=EvidenceTrail(),
                started_at=started_at,
                completed_at=_utc_now(),
                duration_ms=round((time.monotonic() - started) * 1000.0, 3),
            )

    def _persist(self, run: InvestigationRun) -> None:
        persist_result(
            self.connection,
            run.result.incident_id,
            run.result_dict,
            run.result.outcome,
            trail=run.trail,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_ms=run.duration_ms,
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["InvestigationRunner"]
