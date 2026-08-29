"""Human-readable evidence trails for L4 investigations.

A trail is a product surface: it preserves the question, response, order,
latency, and outcome of every gateway call. It is deliberately independent of
logging so a dashboard or judge can render the same evidence path later.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


class EvidenceTrail:
    """An ordered, in-memory collection of gateway trail entries."""

    def __init__(self, entries: Iterable[Mapping[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = [dict(entry) for entry in entries or ()]

    def record(
        self,
        *,
        query_id: str,
        tool: str,
        parameters: Mapping[str, Any],
        response: Mapping[str, Any],
        timestamp: str,
        duration_ms: float,
        outcome: str,
        executed: bool,
    ) -> dict[str, Any]:
        """Append one complete call record and return the stored entry."""
        entry = {
            "sequence": len(self.entries) + 1,
            "query_id": query_id,
            "tool": tool,
            "parameters": dict(parameters),
            "response": dict(response),
            "timestamp": timestamp,
            "duration_ms": round(float(duration_ms), 3),
            "outcome": outcome,
            "executed": bool(executed),
        }
        self.entries.append(entry)
        return entry

    def append(self, entry: Mapping[str, Any]) -> None:
        """Append an existing entry, assigning an order when it has none."""
        copied = dict(entry)
        copied.setdefault("sequence", len(self.entries) + 1)
        self.entries.append(copied)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def render(self) -> str:
        """Render the trail as a readable, stable dashboard-friendly view."""
        return render_trail(self.entries)

    def has_executed(self, query_id: str) -> bool:
        """Return whether a query id corresponds to an actually attempted call."""
        return any(
            entry.get("query_id") == query_id and entry.get("executed", True)
            for entry in self.entries
        )


def render_trail(entries: Iterable[Mapping[str, Any]]) -> str:
    """Render trail entries in recorded order, including failures and refusals."""
    ordered = list(entries)
    lines = ["Evidence trail", "==============="]
    if not ordered:
        lines.append("No evidence queries were run.")
        return "\n".join(lines)

    for index, entry in enumerate(ordered, start=1):
        timestamp = entry.get("timestamp", "unknown time")
        tool = entry.get("tool", "unknown tool")
        outcome = entry.get("outcome", "unknown")
        duration = entry.get("duration_ms", "?")
        query_id = entry.get("query_id", "unassigned")
        lines.append(f"{index}. {timestamp}  {tool}  [{outcome}; {duration} ms]")
        lines.append(f"   query_id: {query_id}")
        lines.append("   asked:")
        lines.extend(_indented_json(entry.get("parameters", {}), "      "))
        lines.append("   response:")
        lines.extend(_indented_json(entry.get("response", {}), "      "))
    return "\n".join(lines)


def _indented_json(value: Any, prefix: str) -> list[str]:
    rendered = json.dumps(value, indent=2, sort_keys=True, default=str)
    return [prefix + line for line in rendered.splitlines()]
