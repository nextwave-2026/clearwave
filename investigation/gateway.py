"""The sole gateway from L4 to the eleven C2 evidence tools.

The gateway owns query identity, allowlisting, subprocess invocation, timeout
handling, budget enforcement, and trail recording. It contains no diagnosis
logic. Tool scripts are invoked exactly like ``stubs/slice.py``: one JSON
object on stdin and one JSON object on stdout.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .trail import EvidenceTrail

ALLOWED_TOOLS = (
    "cohort_metrics",
    "cohort_compare",
    "drilldown",
    "decline_breakdown",
    "retry_stats",
    "operational_metrics",
    "confounding_check",
    "incident_history",
    "external_status",
    "financial_impact",
    "metric_series",
)
OPENING_TOOLS = (
    "cohort_metrics",
    "cohort_compare",
    "decline_breakdown",
    "retry_stats",
    "operational_metrics",
    "confounding_check",
    "financial_impact",
)

Runner = Callable[[str, Mapping[str, Any], float], Any]


class EvidenceGateway:
    """Call C2 tools through one bounded, auditable interface.

    ``runner`` is an optional test seam with the signature
    ``runner(tool_name, parameters, timeout_seconds)``. Its return value may be
    a response mapping, a ``subprocess.CompletedProcess``, or a
    ``(returncode, stdout)`` pair.
    """

    allowed_tools = frozenset(ALLOWED_TOOLS)
    opening_tools = frozenset(OPENING_TOOLS)

    def __init__(
        self,
        *,
        tool_dir: Path | str | None = None,
        cwd: Path | str | None = None,
        query_budget: int = 6,
        timeout_seconds: float = 5.0,
        external_status_timeout_seconds: float = 8.0,
        trail: EvidenceTrail | None = None,
        python_executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        if query_budget < 0:
            raise ValueError("query_budget must be non-negative")
        if timeout_seconds <= 0 or external_status_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        self.tool_dir = Path(tool_dir) if tool_dir is not None else _default_tool_dir()
        self.cwd = Path(cwd) if cwd is not None else self.tool_dir.parent.parent
        self.query_budget = int(query_budget)
        self.timeout_seconds = float(timeout_seconds)
        self.external_status_timeout_seconds = float(external_status_timeout_seconds)
        self.python_executable = python_executable or sys.executable
        self.trail = trail if trail is not None else EvidenceTrail()
        self.runner = runner
        self._additional_calls = 0

    @property
    def remaining_budget(self) -> int:
        """Number of non-opening calls that may still be attempted."""
        return max(0, self.query_budget - self._additional_calls)

    @property
    def additional_calls(self) -> int:
        """Number of non-opening calls consumed, including failed calls."""
        return self._additional_calls

    def query_id_for(self, tool: str, parameters: Any) -> str:
        """Return the stable id for the canonical ``{tool, input}`` value."""
        material = json.dumps(
            {"tool": tool, "input": parameters},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"q_{tool}_{hashlib.sha256(material).hexdigest()[:16]}"

    def call(
        self,
        tool: str,
        parameters: Mapping[str, Any],
        *,
        opening: bool = False,
    ) -> dict[str, Any]:
        """Call one allowlisted tool and return its response or a refusal.

        Refusals and tool failures are returned as JSON-shaped responses, never
        raised as investigation-fatal exceptions. Every call receives a trail
        entry, while ``verify_citation`` only accepts entries that were
        actually attempted.
        """
        started = time.perf_counter()
        timestamp = _utc_now()
        try:
            query_id = self.query_id_for(tool, parameters)
        except (TypeError, ValueError):
            query_id = self.query_id_for(tool, repr(parameters))

        if not isinstance(parameters, Mapping):
            return self._finish(
                query_id,
                tool,
                {},
                _error_response(query_id, "invalid_input", "parameters must be a JSON object"),
                timestamp,
                started,
                "refused",
                False,
            )

        if tool not in self.allowed_tools:
            return self._finish(
                query_id,
                tool,
                parameters,
                _error_response(query_id, "tool_not_allowed", f"tool {tool!r} is not allowed"),
                timestamp,
                started,
                "refused",
                False,
            )

        if not opening and self._additional_calls >= self.query_budget:
            return self._finish(
                query_id,
                tool,
                parameters,
                _error_response(query_id, "budget_exceeded", "evidence query budget exhausted"),
                timestamp,
                started,
                "refused",
                False,
            )

        if not opening:
            self._additional_calls += 1
        timeout = (
            self.external_status_timeout_seconds
            if tool == "external_status"
            else self.timeout_seconds
        )
        response, outcome, executed = self._execute(tool, parameters, query_id, timeout)
        return self._finish(
            query_id,
            tool,
            parameters,
            response,
            timestamp,
            started,
            outcome,
            executed,
        )

    query = call
    call_tool = call

    def run_opening(
        self,
        requests: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Run the fixed opening set without consuming the further-call budget."""
        bundle: dict[str, dict[str, Any]] = {}
        for tool in OPENING_TOOLS:
            if tool in requests:
                bundle[tool] = self.call(tool, requests[tool], opening=True)
        return bundle

    def opening_bundle(
        self,
        incident: Mapping[str, Any],
        window: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Build and run the standard opening evidence set for a C3 incident."""
        cohort = dict(incident.get("affected_cohort", {}))
        incident_window = dict(window or _incident_window(incident))
        requests: dict[str, Mapping[str, Any]] = {
            "cohort_metrics": {"cohort": cohort, "window": incident_window},
            "cohort_compare": {
                "cohort": cohort,
                "window": incident_window,
                "compare_dimensions": [
                    "provider",
                    "payment_method",
                    "country",
                    "card_network",
                    "issuing_bank",
                ],
            },
            "decline_breakdown": {"cohort": cohort, "window": incident_window},
            "retry_stats": {"cohort": cohort, "window": incident_window},
            "operational_metrics": {
                "target": {"kind": "cohort", **cohort},
                "window": incident_window,
            },
            "confounding_check": {
                "dimension_a": "provider",
                "dimension_b": "issuing_bank",
                "window": incident_window,
                "cohort": {"merchant_id": cohort.get("merchant_id")}
                if cohort.get("merchant_id") is not None
                else {},
            },
            "financial_impact": {
                "incident_id": incident.get("incident_id", ""),
                "window": incident_window,
            },
        }
        return self.run_opening(requests)

    def verify_citation(self, query_id: str) -> bool:
        """Return true only when the cited query id was actually attempted."""
        return self.trail.has_executed(query_id)

    def _execute(
        self,
        tool: str,
        parameters: Mapping[str, Any],
        query_id: str,
        timeout: float,
    ) -> tuple[dict[str, Any], str, bool]:
        try:
            raw = self.runner(tool, parameters, timeout) if self.runner else self._subprocess(
                tool, parameters, timeout
            )
        except subprocess.TimeoutExpired:
            return (
                _error_response(query_id, "timeout", f"{tool} exceeded {timeout:g}s timeout"),
                "failure",
                True,
            )
        except (OSError, ValueError, TypeError) as exc:
            return (_error_response(query_id, "tool_error", str(exc)), "failure", True)

        response, returncode = _normalise_runner_result(raw)
        if response is None:
            return (
                _error_response(query_id, "invalid_response", "tool did not return a JSON object"),
                "failure",
                True,
            )
        response = dict(response)
        response["query_id"] = query_id
        if returncode != 0 or "error" in response:
            if "error" not in response:
                response["error"] = {
                    "code": "tool_failed",
                    "message": f"{tool} exited with status {returncode}",
                }
            return response, "failure", True
        return response, "success", True

    def _subprocess(
        self,
        tool: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.python_executable, str(self.tool_dir / f"{tool}.py")]
        return subprocess.run(
            command,
            cwd=self.cwd,
            input=json.dumps(parameters, sort_keys=True, separators=(",", ":")),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _finish(
        self,
        query_id: str,
        tool: str,
        parameters: Mapping[str, Any],
        response: dict[str, Any],
        timestamp: str,
        started: float,
        outcome: str,
        executed: bool,
    ) -> dict[str, Any]:
        duration_ms = (time.perf_counter() - started) * 1000.0
        self.trail.record(
            query_id=query_id,
            tool=tool,
            parameters=parameters,
            response=response,
            timestamp=timestamp,
            duration_ms=duration_ms,
            outcome=outcome,
            executed=executed,
        )
        return response


def _normalise_runner_result(raw: Any) -> tuple[dict[str, Any] | None, int]:
    if isinstance(raw, Mapping):
        return dict(raw), 0
    if isinstance(raw, subprocess.CompletedProcess):
        return _parse_stdout(raw.stdout), int(raw.returncode)
    if isinstance(raw, tuple) and len(raw) == 2:
        return _parse_stdout(raw[1]), int(raw[0])
    return None, 1


def _parse_stdout(stdout: Any) -> dict[str, Any] | None:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if not isinstance(stdout, str):
        return None
    try:
        value = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _error_response(query_id: str, code: str, message: str) -> dict[str, Any]:
    return {"query_id": query_id, "error": {"code": code, "message": message}}


def _default_tool_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "stubs" / "evidence"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _incident_window(incident: Mapping[str, Any]) -> dict[str, str]:
    persistence = incident.get("persistence", {})
    end = persistence.get("last_observed_at") if isinstance(persistence, Mapping) else None
    start = incident.get("onset")
    if not isinstance(start, str) or not start:
        start = end if isinstance(end, str) else _utc_now()
    if not isinstance(end, str) or not end:
        end = start
    return {"start": start, "end": end}
