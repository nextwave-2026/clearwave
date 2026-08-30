"""One fact per piece of the live loop. Wrong line = that piece."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "state" / "clearwave.db"

from prepare_history import cohort_warmth, warmth_lines  # noqa: E402
DASHBOARD = "http://127.0.0.1:8082/api/overview"
WORKERS = (
    ("merchant-a", "clearwave-worker-merchant-a"),
    ("merchant-b", "clearwave-worker-merchant-b"),
    ("merchant-c", "clearwave-worker-merchant-c"),
)


def inspect(name: str, template: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", template, name],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def health(name: str) -> str:
    status = inspect(
        name,
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
    )
    return status if status else "missing"


def running(name: str) -> str:
    status = inspect(name, "{{.State.Status}}")
    return status if status else "missing"


def store_report() -> tuple[str, list[str]]:
    if not STORE.exists():
        missing = f"no database at {STORE}"
        return f"store: {missing}", warmth_lines({}, missing=missing)
    try:
        connection = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        missing = f"cannot open {STORE} ({exc})"
        return f"store: {missing}", warmth_lines({}, missing=missing)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='attempt'"
        ).fetchone()
        if not row or row[0] == 0:
            missing = f"no attempt table in {STORE}"
            return f"store: {missing}", warmth_lines({}, missing=missing)
        count = connection.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
        try:
            connection.row_factory = sqlite3.Row
            history = warmth_lines(cohort_warmth(connection))
        except sqlite3.Error as exc:
            history = warmth_lines({}, missing=f"unreadable ({exc})")
        return f"store: {count} attempt rows in {STORE}", history
    except sqlite3.Error as exc:
        missing = f"{STORE} present but unreadable ({exc})"
        return f"store: {missing}", warmth_lines({}, missing=missing)
    finally:
        connection.close()


def dashboard_line() -> str:
    try:
        with urllib.request.urlopen(DASHBOARD, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        return f"dashboard: not answering {DASHBOARD} ({exc})"
    incidents = body.get("active_incident_count")
    extra = f", active_incident_count={incidents}" if incidents is not None else ""
    return f"dashboard: answering {DASHBOARD}{extra}"


def worker_line() -> str:
    parts = []
    for label, container in WORKERS:
        parts.append(f"{label} {running(container)}")
    return f"workers: {', '.join(parts)}"


def main() -> int:
    print(f"broker: {health('clearwave-kafka')}")
    print(f"schema-registry: {health('clearwave-schema-registry')}")
    print(worker_line())
    print(f"detector: {running('clearwave-detector')}")
    print(f"investigation: {running('clearwave-investigation')}")
    store, history = store_report()
    print(store)
    for line in history:
        print(line)
    print(dashboard_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
