"""Evaluator-only loader for quarantined C6 records.

Detection and investigation must not import this module. It is the missing
read path: `score_diagnosis` still takes a hidden-truth object, and this
module is what fetches that object from the per-merchant stores the worker
writes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from worker.ground_truth import store


class HiddenTruthError(Exception):
    """Base error for evaluator store reads."""


class MissingHiddenTruthError(HiddenTruthError):
    pass


class UnclosedHiddenTruthError(HiddenTruthError):
    pass


class AmbiguousHiddenTruthError(HiddenTruthError):
    def __init__(self, message: str, records: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.records = records


def discover_ground_truth_dbs(root: Path) -> list[Path]:
    """Find per-merchant `ground_truth.db` files without assuming one worker."""
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    found: list[Path] = []
    direct = root / "ground_truth.db"
    if direct.is_file():
        found.append(direct)
    found.extend(sorted(path for path in root.glob("*/ground_truth.db") if path.is_file()))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _summarise(path: Path, instance_id: str, record: dict[str, Any], *, closed: bool) -> dict[str, Any]:
    return {
        "path": str(path),
        "instance_id": instance_id,
        "scenario_id": record.get("scenario_id"),
        "closed": closed,
    }


def _is_closed(record: dict[str, Any]) -> bool:
    return "observed" in record and record["observed"] is not None


def load_hidden_truth(
    *,
    store_path: Path | str | None = None,
    store_dir: Path | str | None = None,
    instance_id: str | None = None,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """Load one closed C6 record, or refuse if the match is missing, open, or ambiguous."""
    paths: list[Path] = []
    if store_path is not None:
        paths.append(Path(store_path))
    if store_dir is not None:
        paths.extend(discover_ground_truth_dbs(Path(store_dir)))
    if not paths:
        raise MissingHiddenTruthError("no ground-truth store path given")

    matches: list[tuple[Path, str, dict[str, Any]]] = []
    closed_matches: list[tuple[Path, str, dict[str, Any]]] = []
    unclosed_matches: list[tuple[Path, str, dict[str, Any]]] = []
    for path in paths:
        connection = store.connect(path, readonly=True)
        try:
            listings = store.list_hidden_truth(connection)
            for listing in listings:
                record = store.read_hidden_truth(connection, listing["instance_id"])
                if record is None:
                    continue
                if instance_id is not None and listing["instance_id"] != instance_id:
                    continue
                if scenario_id is not None and record.get("scenario_id") != scenario_id:
                    continue
                item = (path, listing["instance_id"], record)
                matches.append(item)
                if _is_closed(record):
                    closed_matches.append(item)
                else:
                    unclosed_matches.append(item)
        finally:
            connection.close()

    if instance_id is not None and not matches:
        raise MissingHiddenTruthError(f"no hidden-truth record with instance_id {instance_id!r}")
    if scenario_id is not None and not matches:
        raise MissingHiddenTruthError(f"no hidden-truth record with scenario_id {scenario_id!r}")
    if not matches:
        raise MissingHiddenTruthError("no hidden-truth records in the given store(s)")

    if len(closed_matches) == 1:
        return closed_matches[0][2]
    if len(closed_matches) > 1:
        summaries = [
            _summarise(path, iid, record, closed=True) for path, iid, record in closed_matches
        ]
        raise AmbiguousHiddenTruthError(
            "multiple closed hidden-truth records; pass --instance-id or --scenario-id",
            summaries,
        )
    summaries = [
        _summarise(path, iid, record, closed=False) for path, iid, record in unclosed_matches
    ]
    raise UnclosedHiddenTruthError(
        "hidden-truth record exists but the scenario window is still open"
        + (f": {summaries[0]['instance_id']}" if len(summaries) == 1 else "")
    )
