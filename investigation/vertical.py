"""Run the real seed -> detect -> investigate chain with no fixture stand-ins.

    python3 -m investigation.vertical

To investigate a store that has *already* been detected against - a live Kafka
run, or any prepared store - without reseeding it:

    python3 -m investigation.vertical --investigate-only
    python3 -m investigation.vertical --incident-id inc-2026-08-30-715ab9c3

Without OPENAI_API_KEY the investigation agent cannot run. That is the
correct venue-network behaviour: opening evidence still executes against
the measured store, the ledger is seeded from the pre-filter, and the
result is the deterministic ``agent_unavailable`` degrade. The command
never constructs the OpenAI client without a key, and never presents a
degraded run as though a model produced it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from detector.cli import main as detector_main
from detector.store import list_incidents as list_detector_incidents
from detector.store import load_incident

from .agent import InvestigationAgent, InvestigationRun
from .contracts import InvestigationResult
from .env import (
    MISSING_KEY_MESSAGE,
    api_key_present,
    load_dotenv,
    redact_secrets,
)
from .gateway import EvidenceGateway
from .runner import InvestigationRunner
from .store import CLAIMABLE_STATES
from .store import connect as investigation_connect
from .store import model_call_summary, read_result

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "state" / "vertical.db"


class UnavailableClient:
    """Injected-client seam: fail immediately so the agent degrades without network."""

    def __init__(self, message: str = MISSING_KEY_MESSAGE) -> None:
        self.responses = _UnavailableResponses(message)


class _UnavailableResponses:
    def __init__(self, message: str) -> None:
        self._message = message

    def create(self, **kwargs: Any) -> Any:
        raise RuntimeError(self._message)


@dataclass
class VerticalOutcome:
    """Structured result of one seed -> detect -> investigate run."""

    mode: str
    api_key_present: bool
    database: Path
    detected_incidents: list[dict[str, Any]]
    incident: dict[str, Any]
    lifecycle_after_detect: str
    lifecycle_after_investigate: str
    result: dict[str, Any]
    trail: list[dict[str, Any]] = field(default_factory=list)
    run: InvestigationRun | None = None
    path: str = "vertical-path"
    model_calls: int = 0
    store_model_calls: int = 0

    @property
    def outcome(self) -> str:
        return str(self.result.get("outcome", ""))


def reset_database(path: Path) -> None:
    """Start from an empty store so seed and detect are repeatable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def run_detector(argv: list[str]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = detector_main(argv)
    if code:
        raise RuntimeError(f"detector {' '.join(argv)} exited {code}")
    return buffer.getvalue()


def seed_and_detect(db_path: Path) -> list[dict[str, Any]]:
    """Run the real detector CLI against one store and return detected incidents."""
    resolved = db_path.resolve()
    os.environ["CLEARWAVE_DB"] = str(resolved)
    run_detector(["--db", str(resolved), "seed"])
    run_detector(["--db", str(resolved), "detect"])
    connection = investigation_connect(resolved)
    try:
        detector_rows = list_detector_incidents(connection)
    finally:
        connection.close()
    return [row for row in detector_rows if row.get("lifecycle_state") == "detected"]


def list_detected(db_path: Path) -> list[dict[str, Any]]:
    """Claimable C3 records already in the store, newest onset first."""
    resolved = db_path.resolve()
    connection = investigation_connect(resolved)
    try:
        rows = list_detector_incidents(connection)
    finally:
        connection.close()
    return [row for row in rows if row.get("lifecycle_state") in CLAIMABLE_STATES]


def select_detected(
    detected: list[dict[str, Any]],
    incident_id: str | None = None,
) -> dict[str, Any]:
    """Pick the named claimable record, or the newest one when unnamed."""
    if not detected:
        raise RuntimeError(
            "this store holds no incident with lifecycle_state watching or detected. "
            "Run `python3 -m detector detect` (or `detector consume --detect`) against the same "
            "CLEARWAVE_DB first."
        )
    if incident_id is None:
        return dict(detected[0])
    for row in detected:
        if str(row.get("incident_id")) == incident_id:
            return dict(row)
    known = ", ".join(str(row.get("incident_id")) for row in detected)
    raise RuntimeError(
        f"no watching or detected incident {incident_id} in this store. Claimable: {known}"
    )


def investigate_store(
    db_path: Path,
    *,
    use_model: bool | None = None,
    incident_ids: list[str] | None = None,
) -> tuple[str, list[InvestigationRun]]:
    """Claim watches and detected incidents through the real runner.

    ``use_model`` defaults to whether ``OPENAI_API_KEY`` is set. Without a key
    the injected unavailable client is used so OpenAI is never constructed.
    """
    resolved = db_path.resolve()
    os.environ["CLEARWAVE_DB"] = str(resolved)
    model = api_key_present() if use_model is None else use_model
    if model and not api_key_present():
        raise RuntimeError(MISSING_KEY_MESSAGE)
    if model:
        agent = InvestigationAgent()
        mode = "model"
    else:
        agent = InvestigationAgent(client=UnavailableClient())
        mode = "agent_unavailable"
    connection = investigation_connect(resolved)
    runner = InvestigationRunner(connection, agent, incident_ids=incident_ids)
    try:
        runs = runner.poll_once(wait=True)
    finally:
        runner.close()
        connection.close()
    return mode, runs


def execute_vertical_path(
    db_path: Path,
    *,
    recreate: bool = True,
    use_model: bool | None = None,
) -> VerticalOutcome:
    """Seed, detect, investigate, and return the operator-facing outcome."""
    resolved = db_path.resolve()
    if recreate:
        reset_database(resolved)
    detected = seed_and_detect(resolved)
    if not detected:
        raise RuntimeError("vertical-path: detector produced no incident with lifecycle_state detected")
    incident = dict(detected[0])
    return _investigate_detected(
        resolved,
        detected,
        incident,
        use_model=use_model,
        incident_ids=None,
        label="vertical-path",
    )


def execute_investigation_only(
    db_path: Path,
    *,
    incident_id: str | None = None,
    use_model: bool | None = None,
) -> VerticalOutcome:
    """Investigate one already-stored watch or incident. No seed, no detect.

    This is the join between a store that detection already wrote to - a live
    Kafka run included - and one investigation against that stored record.
    The store is never reset and never reseeded.
    """
    resolved = db_path.resolve()
    if not resolved.exists():
        raise RuntimeError(f"no store at {resolved}")
    os.environ["CLEARWAVE_DB"] = str(resolved)
    detected = list_detected(resolved)
    incident = select_detected(detected, incident_id)
    return _investigate_detected(
        resolved,
        detected,
        incident,
        use_model=use_model,
        incident_ids=[str(incident.get("incident_id"))],
        label="investigate-only",
    )


def _investigate_detected(
    resolved: Path,
    detected: list[dict[str, Any]],
    incident: dict[str, Any],
    *,
    use_model: bool | None,
    incident_ids: list[str] | None,
    label: str,
) -> VerticalOutcome:
    lifecycle_after_detect = str(incident.get("lifecycle_state", ""))
    mode, runs = investigate_store(resolved, use_model=use_model, incident_ids=incident_ids)
    if not runs:
        raise RuntimeError(f"{label}: investigation runner claimed no incident")
    run = runs[0]
    connection = investigation_connect(resolved)
    try:
        stored = load_incident(connection, run.result.incident_id) or {}
        persisted = read_result(connection, run.result.incident_id) or {}
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?",
            (run.result.incident_id,),
        ).fetchone()
        lifecycle_after = str(row["lifecycle_state"] if row is not None else "")
        cost = model_call_summary(connection)
    finally:
        connection.close()
    result = run.result_dict
    trail = list(run.trail.entries)
    if persisted.get("result"):
        result = dict(persisted["result"])
    if persisted.get("trail"):
        trail = list(persisted["trail"])
    return VerticalOutcome(
        path=label,
        mode=mode,
        api_key_present=api_key_present(),
        database=resolved,
        detected_incidents=detected,
        incident=dict(stored) if stored else incident,
        lifecycle_after_detect=lifecycle_after_detect,
        lifecycle_after_investigate=lifecycle_after,
        result=result,
        trail=trail,
        run=run,
        model_calls=next(
            (
                item["model_calls"]
                for item in cost.get("by_incident", [])
                if item.get("incident_id") == run.result.incident_id
            ),
            0,
        ),
        store_model_calls=int(cost.get("total", 0)),
    )


def citations_from(value: Any):
    """Yield every evidence item that names a query_id."""
    if isinstance(value, Mapping):
        if "query_id" in value or "tool" in value:
            yield value
        for nested in value.values():
            yield from citations_from(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from citations_from(nested)


def citations_verify_against_trail(result: Mapping[str, Any], trail: list[Mapping[str, Any]]) -> list[str]:
    """Return query ids that are cited but were not executed on the trail."""
    executed = {
        str(entry.get("query_id"))
        for entry in trail
        if entry.get("query_id") and entry.get("executed", True)
    }
    missing: list[str] = []
    for citation in citations_from(result):
        query_id = str(citation.get("query_id", ""))
        if not query_id:
            missing.append("<missing query_id>")
            continue
        if query_id not in executed:
            missing.append(query_id)
    return missing


def format_report(outcome: VerticalOutcome) -> str:
    """Operator-facing summary. Degraded runs are labelled as such."""
    incident = outcome.incident
    result = outcome.result
    change = incident.get("change") or {}
    cohort = incident.get("affected_cohort") or {}
    money = incident.get("financial_impact") or {}
    gmv = money.get("gmv_at_risk") or {}
    loss = money.get("loss_per_hour") or {}
    where = ", ".join(f"{key}={cohort[key]}" for key in sorted(cohort)) or "unlocalised"
    expected = change.get("expected")
    actual = change.get("actual")
    metric = change.get("metric", "payment_approval_conversion")
    hypothesis = (result.get("leading_hypothesis") or {}).get("statement", "")
    confidence = result.get("diagnostic_confidence", "")
    competing = result.get("competing_explanations") or []
    missing = result.get("missing_evidence") or []
    action = result.get("recommended_next_action") or {}
    executed = sum(1 for entry in outcome.trail if entry.get("executed", True))
    unverified = citations_verify_against_trail(result, outcome.trail)

    if outcome.mode == "model" and outcome.outcome != "agent_unavailable":
        mode_lines = [
            "MODE: model",
            "OPENAI_API_KEY is set. The investigation agent ran.",
        ]
    elif outcome.api_key_present:
        mode_lines = [
            "MODE: model attempted, result degraded",
            "OPENAI_API_KEY is set, but this narrative was not produced by the model.",
            f"Investigation outcome: {outcome.outcome}",
        ]
    else:
        mode_lines = [
            "MODE: agent_unavailable",
            MISSING_KEY_MESSAGE,
            "The investigation model did not run.",
            "This is the deterministic degrade path, not a model diagnosis.",
        ]

    competing_lines = (
        [f"  - {item.get('explanation', '')}" for item in competing]
        if competing
        else ["  (none)"]
    )
    missing_lines = (
        [f"  - {item.get('request', '')} ({item.get('reason', '')})" for item in missing]
        if missing
        else ["  (none)"]
    )
    citation_line = (
        "Citations: all cited query ids verify against the gateway trail"
        if not unverified
        else f"Citations: {len(unverified)} cited query ids are missing from the trail"
    )
    title = (
        "Clearwave investigation of a stored incident"
        if outcome.path == "investigate-only"
        else "Clearwave vertical path"
    )
    lines = [
        title,
        "=" * len(title),
        *mode_lines,
        "",
        f"Store: {outcome.database}",
        f"Incident: {incident.get('incident_id', '')}",
        f"Lifecycle after detect: {outcome.lifecycle_after_detect}",
        f"Lifecycle after investigate: {outcome.lifecycle_after_investigate}",
        f"Investigation outcome: {outcome.outcome}",
        "",
        "What changed:",
        f"  {metric}  {expected} -> {actual}  (absolute_delta {change.get('absolute_delta')})",
        "Where:",
        f"  {where}",
        "How much it matters:",
        f"  severity {incident.get('severity')}; "
        f"GMV at risk {gmv.get('amount')} {gmv.get('currency')}; "
        f"loss per hour {loss.get('amount')} {loss.get('currency')}",
        "",
        "Leading hypothesis:",
        f"  {hypothesis}",
        f"Diagnostic confidence: {confidence}",
        "Competing explanations:",
        *competing_lines,
        "Missing evidence:",
        *missing_lines,
        "Recommended action:",
        f"  {action.get('action', '')} (urgency: {action.get('urgency', '')})",
        "",
        f"Evidence trail: {executed} executed queries",
        citation_line,
        f"Model calls for this record: {outcome.model_calls}",
        f"Model calls in this store: {outcome.store_model_calls}",
    ]
    return redact_secrets("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="investigation.vertical", description=__doc__)
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite store path (default: $CLEARWAVE_DB or state/vertical.db)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="do not recreate the store before seeding (still seeds and detects)",
    )
    parser.add_argument(
        "--investigate-only",
        action="store_true",
        help="investigate an already-detected incident in a prepared store; never seeds or detects",
    )
    parser.add_argument(
        "--incident-id",
        default=None,
        help="investigate this stored incident id (implies --investigate-only; "
        "default is the newest detected incident)",
    )
    args = parser.parse_args(argv)
    load_dotenv()
    db_path = Path(args.db or os.environ.get("CLEARWAVE_DB") or DEFAULT_DB)
    investigate_only = args.investigate_only or args.incident_id is not None
    try:
        if investigate_only:
            outcome = execute_investigation_only(db_path, incident_id=args.incident_id)
        else:
            outcome = execute_vertical_path(db_path, recreate=not args.keep)
    except RuntimeError as exc:
        message = redact_secrets(str(exc))
        if "OPENAI_API_KEY" in message:
            print(MISSING_KEY_MESSAGE, file=sys.stderr)
            return 1
        prefix = "investigate-only" if investigate_only else "vertical-path"
        print(f"{prefix}: {message}", file=sys.stderr)
        return 1
    print(format_report(outcome))
    InvestigationResult.model_validate(outcome.result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
