#!/bin/sh
exec python3 - "$@" <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


GUIDE = "docs/integration-guide.md"
TOOLS = {
    "cohort_metrics": {"cohort": {}, "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}},
    "cohort_compare": {"cohort": {}, "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}, "compare_dimensions": []},
    "drilldown": {"incident_id": "inc-2026-08-29-001"},
    "decline_breakdown": {"cohort": {}, "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}},
    "retry_stats": {"cohort": {}, "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}},
    "operational_metrics": {"target": {"kind": "service"}, "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}},
    "confounding_check": {"dimension_a": "provider", "dimension_b": "issuing_bank", "window": {"start": "2026-08-29T10:00:00Z", "end": "2026-08-29T10:15:00Z"}},
    "incident_history": {"merchant_id": "merchant-a"},
    "external_status": {"provider": "provider-p2"},
    "financial_impact": {"incident_id": "inc-2026-08-29-001"},
}
STAGES = (
    "=== STAGE 1 - canonical events ===",
    "=== STAGE 2 - incident record ===",
    "=== STAGE 3 - evidence bundle ===",
    "=== STAGE 4 - investigation result ===",
    "=== STAGE 5 - surface summary and escalation ===",
)
INCIDENT_FIELDS = {
    "incident_id",
    "affected_cohort",
    "change",
    "onset",
    "persistence",
    "blast_radius",
    "financial_impact",
    "severity",
    "lifecycle_state",
}
RESULT_FIELDS = {
    "incident_id",
    "confirmed_facts",
    "leading_hypothesis",
    "supporting_evidence",
    "competing_explanations",
    "why_ambiguity_exists",
    "missing_evidence",
    "diagnostic_confidence",
    "recommended_next_action",
}


def repository_root() -> Path:
    try:
        return Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip())
    except (OSError, subprocess.CalledProcessError):
        print(
            "slice contract: repository root unavailable; run this guard from the repository. "
            f"Read {GUIDE} before rerunning CI.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def fail(message: str) -> None:
    print(f"slice contract: {message} Read {GUIDE} before changing a seam.", file=sys.stderr)
    raise SystemExit(1)


def short_output(value: str) -> str:
    line = value.strip().splitlines()
    return line[0][:240] if line else "no output"


def stage_object(output: str, marker: str) -> dict[str, Any]:
    start = output.find(marker)
    if start < 0:
        fail(f"stubs/slice.py: missing stage marker {marker!r}; restore all five stages and rerun the slice")
    next_markers = [position for position in (output.find(item, start + len(marker)) for item in STAGES) if position >= 0]
    end = min(next_markers) if next_markers else len(output)
    fragment = output[start + len(marker):end]
    json_start = fragment.find("{")
    if json_start < 0:
        fail(f"stubs/slice.py: stage {marker!r} emitted no JSON object; restore its contract output")
    try:
        value, _ = json.JSONDecoder().raw_decode(fragment[json_start:])
    except json.JSONDecodeError as error:
        fail(f"stubs/slice.py: stage {marker!r} emitted invalid JSON ({error}); restore its contract output")
    if not isinstance(value, dict):
        fail(f"stubs/slice.py: stage {marker!r} must emit a JSON object; restore its contract output")
    return value


def tool_path(root: Path, name: str) -> Path:
    path = root / "stubs" / "evidence" / f"{name}.py"
    if not path.is_file():
        fail(f"{path.relative_to(root)}: evidence tool entry point is missing; restore it and rerun the slice")
    if not os.access(path, os.X_OK):
        fail(f"{path.relative_to(root)}: evidence tool entry point is not executable; restore executable permission")
    return path


def run_tool(root: Path, name: str, request: dict[str, Any]) -> None:
    path = tool_path(root, name)
    try:
        completed = subprocess.run(
            [str(path)],
            cwd=root,
            input=json.dumps(request),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"{path.relative_to(root)}: evidence tool could not complete ({error}); restore its JSON entry point")
    if completed.returncode != 0:
        fail(
            f"{path.relative_to(root)}: minimal valid JSON call exited {completed.returncode} "
            f"({short_output(completed.stderr or completed.stdout)}); preserve the documented tool contract"
        )
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"{path.relative_to(root)}: minimal valid JSON call returned invalid JSON ({error}); emit one JSON object only")
    if not isinstance(response, dict):
        fail(f"{path.relative_to(root)}: response must be a JSON object; preserve the documented response shape")
    for field in ("query_id", "as_of"):
        if not isinstance(response.get(field), str) or not response[field].strip():
            fail(
                f"{path.relative_to(root)}: response omitted a non-empty {field}; preserve the citation trail "
                "and return both query_id and as_of"
            )


def main() -> None:
    root = repository_root()
    for name in TOOLS:
        tool_path(root, name)

    slice_path = root / "stubs" / "slice.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(slice_path)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(f"{slice_path.relative_to(root)}: integration smoke test could not complete ({error}); restore the runnable slice")
    if completed.returncode != 0:
        fail(
            f"{slice_path.relative_to(root)}: integration smoke test exited {completed.returncode} "
            f"({short_output(completed.stderr or completed.stdout)}); restore the five-stage path"
        )
    for marker in STAGES:
        if marker not in completed.stdout:
            fail(f"{slice_path.relative_to(root)}: output is missing stage marker {marker!r}; restore all five stages")

    incident = stage_object(completed.stdout, STAGES[1])
    missing_incident = sorted(INCIDENT_FIELDS - incident.keys())
    if missing_incident:
        fail(
            f"{slice_path.relative_to(root)} and docs/contracts/incident.md: incident record is missing "
            f"top-level field(s) {', '.join(missing_incident)}; restore the C3 shape"
        )
    if "diagnostic_confidence" in incident:
        fail(
            f"{slice_path.relative_to(root)} and docs/contracts/incident.md: incident record contains "
            "diagnostic_confidence; keep causal confidence in C4 and restore the C3 shape"
        )

    result = stage_object(completed.stdout, STAGES[3])
    missing_result = sorted(RESULT_FIELDS - result.keys())
    if missing_result:
        fail(
            f"{slice_path.relative_to(root)} and docs/contracts/investigation-result.md: investigation result is missing "
            f"top-level field(s) {', '.join(missing_result)}; restore the C4 shape"
        )
    if "severity" in result:
        fail(
            f"{slice_path.relative_to(root)} and docs/contracts/investigation-result.md: investigation result contains "
            "severity; keep business priority in C3 and restore the C4 shape"
        )

    for name, request in TOOLS.items():
        run_tool(root, name, request)

    print("slice contract: five-stage slice, ten evidence tools, citation fields, and C3/C4 shapes verified")


if __name__ == "__main__":
    main()
PY
