"""Evaluator read path across per-merchant stores, and isolation from W2/W3."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from evaluator.score import main as score_main, score_diagnosis
from evaluator.truth import (
    AmbiguousHiddenTruthError,
    MissingHiddenTruthError,
    UnclosedHiddenTruthError,
    discover_ground_truth_dbs,
    load_hidden_truth,
)
from worker.ground_truth import store
from worker.ground_truth.runner import ScenarioRun


ROOT = Path(__file__).resolve().parents[1]


def _write_closed(db_path: Path, scenario_id: str, duration: int = 12) -> ScenarioRun:
    run = ScenarioRun(scenario_id, duration_seconds=duration, db_path=db_path)
    run.close()
    return run


def _compose_services(text: str) -> dict[str, str]:
    """Map compose service name to the raw block under it."""
    services: dict[str, str] = {}
    in_services = False
    name: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("services:"):
            in_services = True
            continue
        if not in_services:
            continue
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            break
        if (
            line.startswith("  ")
            and not line.startswith("    ")
            and line.rstrip().endswith(":")
            and not line.strip().startswith("#")
            and not line.strip().startswith("-")
        ):
            if name is not None:
                services[name] = "\n".join(buf)
            name = line.strip().rstrip(":")
            buf = []
            continue
        buf.append(line)
    if name is not None:
        services[name] = "\n".join(buf)
    return services


def _yaml_list_entries(block: str, key: str) -> list[str | dict[str, str]]:
    """Parse a compose list of scalars or single-level maps under `key`."""
    entries: list[str | dict[str, str]] = []
    key_indent: int | None = None
    item_indent: int | None = None
    current: dict[str, str] | None = None

    def finish() -> None:
        nonlocal current
        if current is not None:
            entries.append(current)
            current = None

    for line in block.splitlines():
        raw = line.rstrip()
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if key_indent is None:
            if stripped == f"{key}:" or stripped.startswith(f"{key}:"):
                key_indent = indent
            continue
        if indent <= key_indent:
            finish()
            break
        if stripped.startswith("- "):
            finish()
            item_indent = indent
            rest = stripped[2:]
            if rest.endswith(":") or ": " in rest:
                field, _, value = rest.partition(":")
                current = {field.strip(): value.strip()}
            else:
                entries.append(rest)
            continue
        if current is not None and item_indent is not None and indent > item_indent:
            field, _, value = stripped.partition(":")
            current[field.strip()] = value.strip()
            continue
        finish()
        break
    finish()
    return entries


def _bind_mounts(block: str) -> list[tuple[str, str]]:
    mounts: list[tuple[str, str]] = []
    for entry in _yaml_list_entries(block, "volumes"):
        if isinstance(entry, str):
            parts = entry.split(":")
            if len(parts) < 2:
                continue
            source, target = parts[0], parts[1]
            if source.startswith(".") or source.startswith("/"):
                mounts.append((source, target))
        elif entry.get("type", "bind") == "bind":
            source = entry.get("source") or entry.get("src")
            target = entry.get("target") or entry.get("destination")
            if source and target:
                mounts.append((source, target))
    return mounts


def _mask_targets(block: str) -> set[str]:
    masks: set[str] = set()
    for entry in _yaml_list_entries(block, "tmpfs"):
        if isinstance(entry, str):
            masks.add(entry.rstrip("/"))
        else:
            target = entry.get("target") or entry.get("destination")
            if target:
                masks.add(target.rstrip("/"))
    for entry in _yaml_list_entries(block, "volumes"):
        if isinstance(entry, dict) and entry.get("type") == "tmpfs":
            target = entry.get("target") or entry.get("destination")
            if target:
                masks.add(target.rstrip("/"))
    return masks


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class MultiWorkerStoreTests(unittest.TestCase):
    def test_discover_does_not_assume_a_single_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "merchant-a").mkdir()
            (root / "merchant-b").mkdir()
            (root / "merchant-c").mkdir()
            (root / "merchant-a" / "ground_truth.db").write_bytes(b"")
            (root / "merchant-c" / "ground_truth.db").write_bytes(b"")
            found = {path.parent.name for path in discover_ground_truth_dbs(root)}
            self.assertEqual(found, {"merchant-a", "merchant-c"})

    def test_load_picks_the_only_closed_record_across_merchants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_db = root / "merchant-a" / "ground_truth.db"
            c_db = root / "merchant-c" / "ground_truth.db"
            a_db.parent.mkdir()
            c_db.parent.mkdir()
            store.connect(a_db).close()
            closed = _write_closed(c_db, "provider-degradation")
            record = load_hidden_truth(store_dir=root)
            self.assertEqual(record["scenario_id"], "provider-degradation")
            self.assertEqual(record["injected"]["affected_cohort"]["provider"], "adyen")
            self.assertIn("observed", record)
            self.assertTrue(closed._closed)

    def test_load_refuses_an_open_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "merchant-c" / "ground_truth.db"
            db_path.parent.mkdir()
            opened = ScenarioRun(
                "provider-degradation", duration_seconds=30, db_path=db_path
            )
            opened._conn.close()
            with self.assertRaises(UnclosedHiddenTruthError):
                load_hidden_truth(store_path=db_path)

    def test_load_refuses_to_guess_among_several_closed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_db = root / "merchant-a" / "ground_truth.db"
            c_db = root / "merchant-c" / "ground_truth.db"
            a_db.parent.mkdir()
            c_db.parent.mkdir()
            a_run = _write_closed(a_db, "high-impact-small-percentage")
            c_run = _write_closed(c_db, "provider-degradation")
            with self.assertRaises(AmbiguousHiddenTruthError) as raised:
                load_hidden_truth(store_dir=root)
            instance_ids = {item["instance_id"] for item in raised.exception.records}
            self.assertEqual(instance_ids, {a_run.instance_id, c_run.instance_id})
            chosen = load_hidden_truth(store_dir=root, instance_id=c_run.instance_id)
            self.assertEqual(chosen["scenario_id"], "provider-degradation")
            by_scenario = load_hidden_truth(
                store_dir=root, scenario_id="high-impact-small-percentage"
            )
            self.assertEqual(by_scenario["scenario_id"], "high-impact-small-percentage")

    def test_missing_store_is_an_error_not_a_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MissingHiddenTruthError):
                load_hidden_truth(store_dir=tmp)

    def test_evaluator_scores_a_store_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "merchant-c" / "ground_truth.db"
            db_path.parent.mkdir()
            _write_closed(db_path, "provider-degradation")
            hidden = load_hidden_truth(store_dir=tmp)
            diagnosis = {
                "diagnosed_cohort": {"provider": "adyen"},
                "investigation_result": {
                    "leading_hypothesis": {"statement": "Adyen is degraded."},
                    "competing_explanations": [],
                    "missing_evidence": [],
                    "diagnostic_confidence": "high",
                },
            }
            result = score_diagnosis(diagnosis, hidden)
            self.assertEqual(result["verdict"], "pass")
            self.assertEqual(result["components"]["cohort_localisation"]["precision"], 1.0)
            self.assertEqual(result["components"]["cohort_localisation"]["recall"], 1.0)

    def test_cli_store_dir_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "merchant-c" / "ground_truth.db"
            db_path.parent.mkdir()
            _write_closed(db_path, "provider-degradation")
            diagnosis_path = root / "diagnosis.json"
            diagnosis_path.write_text(
                json.dumps({"diagnosed_cohort": {"provider": "adyen"}}),
                encoding="utf-8",
            )
            code = score_main(
                [str(diagnosis_path), "--store-dir", str(root)]
            )
            self.assertEqual(code, 0)


class IsolationTests(unittest.TestCase):
    FORBIDDEN = (
        "worker.ground_truth",
        "worker.ground_truth.store",
        "worker.ground_truth.runner",
        "worker.ground_truth.scenarios",
        "evaluator.truth",
        "evaluator.score",
        "evaluator",
    )

    def test_detector_has_no_hidden_truth_import(self) -> None:
        self._assert_tree_clean(ROOT / "detector")

    def test_investigation_has_no_hidden_truth_import(self) -> None:
        self._assert_tree_clean(ROOT / "investigation")

    def test_compose_mounts_hidden_truth_only_on_workers(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("./state/ground_truth/merchant-a:/hidden-truth", compose)
        self.assertIn("./state/ground_truth/merchant-b:/hidden-truth", compose)
        self.assertIn("./state/ground_truth/merchant-c:/hidden-truth", compose)
        self.assertIn("CLEARWAVE_GROUND_TRUTH_DB: /hidden-truth/ground_truth.db", compose)
        services = _compose_services(compose)
        seen_state_bind = False
        seen_repo_bind = False
        for name, block in services.items():
            if name.startswith("worker-"):
                continue
            self.assertNotIn(
                "CLEARWAVE_GROUND_TRUTH_DB",
                block,
                msg=f"{name} must not receive the ground-truth environment variable",
            )
            masks = _mask_targets(block)
            for source, target in _bind_mounts(block):
                source_path = source.rstrip("/")
                target_path = target.rstrip("/")
                if source_path in {"./state", "state"}:
                    seen_state_bind = True
                    required = f"{target_path}/ground_truth"
                    self.assertIn(
                        required,
                        masks,
                        msg=(
                            f"{name} bind-mounts {source} at {target} without "
                            f"masking {required}"
                        ),
                    )
                if source_path in {".", "./"}:
                    seen_repo_bind = True
                    required = f"{target_path}/state/ground_truth"
                    self.assertIn(
                        required,
                        masks,
                        msg=(
                            f"{name} bind-mounts repo root at {target} without "
                            f"masking {required}"
                        ),
                    )
        self.assertTrue(
            seen_state_bind,
            "parser found no ./state bind; the compose walker is broken",
        )
        self.assertTrue(
            seen_repo_bind,
            "parser found no repo-root bind; the compose walker is broken",
        )

    def test_investigation_image_has_no_hidden_truth_path(self) -> None:
        dockerfile = (ROOT / "investigation" / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("ground_truth", dockerfile)
        self.assertNotIn("CLEARWAVE_GROUND_TRUTH_DB", dockerfile)
        self.assertNotIn("worker/", dockerfile)

    def _assert_tree_clean(self, tree: Path) -> None:
        offenders: list[str] = []
        for path in tree.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            imported = _imported_modules(path)
            for name in imported:
                if name in self.FORBIDDEN or name.startswith("worker.ground_truth"):
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
