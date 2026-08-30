#!/usr/bin/env python3
"""Prepare and start the deterministic judge fallback.

The fallback writes healthy live-vocabulary history, appends a finite block of
real simulated attempts whose approval rate is deliberately developing, and
runs the production detector against that store. It refuses to start unless
that detector persists exactly one watching record and no incident. The stack
then starts normally; a judge can press Collapse to add a live second stage,
which upgrades the stored watch through the normal Kafka path.

The prepared attempts are data, not a UI fixture or a prewritten C3 record.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "fallback-demo"
DB_PATH = STATE_DIR / "clearwave.db"
COMPOSE_BASE = ROOT / "docker-compose.yml"
COMPOSE_OVERLAY = ROOT / "scripts" / "fallback-demo.compose.yml"
PROJECT = "clearwave-fallback-demo"
SURFACES_PORT = 38082
STAGE_MINUTES = 3
STAGE_ATTEMPTS_PER_MINUTE = 80
STAGE_APPROVAL_PROBABILITY = 0.80
STAGE_SEED = 1

SERVICES = (
    "kafka",
    "schema-registry",
    "worker-merchant-b",
    "detector",
    "investigation",
    "surfaces",
)

sys.path.insert(0, str(ROOT))
from detector import cli, config, store  # noqa: E402
from scripts import prepare_history  # noqa: E402
from tests.synthetic import iter_live_healthy_history  # noqa: E402


def compose(*args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--file",
        str(COMPOSE_BASE),
        "--file",
        str(COMPOSE_OVERLAY),
        *args,
    ]


def run_compose(*args: str, timeout: int = 600) -> None:
    completed = subprocess.run(compose(*args), cwd=ROOT, check=False, text=True)
    if completed.returncode:
        raise SystemExit(f"fallback: docker compose {' '.join(args)} exited {completed.returncode}")


def reset_stack() -> None:
    print(f"fallback: stopping any previous {PROJECT} stack", flush=True)
    run_compose("down", "--volumes", "--remove-orphans", timeout=180)
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    for merchant in ("merchant-a", "merchant-b", "merchant-c"):
        (STATE_DIR / "ground_truth" / merchant).mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o777)


def staged_events(anchor: datetime) -> list[dict[str, Any]]:
    """Return real canonical attempts for the finite developing input.

    Templates come from the same live-vocabulary history used to warm the
    store, preserving the merchant's actual dimensions and currency. Only the
    event identity, event time and outcome change. ``timeout`` is the C1b
    reason; ``provider_timeout`` remains the native code in the evidence.
    """
    history = list(
        iter_live_healthy_history(
            hours=8.0,
            per_merchant_per_minute=24,
            seed=20260830,
            as_of=anchor,
        )
    )
    templates = [
        event
        for event in history
        if event["merchant_id"] == "merchant-b" and event["provider"] == "adyen"
    ]
    needed = STAGE_MINUTES * STAGE_ATTEMPTS_PER_MINUTE
    if len(templates) < needed:
        raise RuntimeError(f"fallback: only {len(templates)} live templates, need {needed}")

    rng = random.Random(STAGE_SEED)
    events: list[dict[str, Any]] = []
    index = 0
    for minute in range(STAGE_MINUTES):
        event_minute = anchor - timedelta(minutes=STAGE_MINUTES - minute)
        for slot in range(STAGE_ATTEMPTS_PER_MINUTE):
            index += 1
            event = dict(templates[(minute * STAGE_ATTEMPTS_PER_MINUTE + slot) % len(templates)])
            approved = rng.random() < STAGE_APPROVAL_PROBABILITY
            event["payment_method"] = "pse"
            event["card_network"] = None
            event.update(
                event_id=f"fallback-{STAGE_SEED}-{index:05d}",
                payment_id=f"pay-fallback-{STAGE_SEED}-{index:05d}",
                attempt_id=f"att-fallback-{STAGE_SEED}-{index:05d}-1",
                occurred_at=(event_minute + timedelta(seconds=slot)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                status="approved" if approved else "declined",
                latency_ms=220,
            )
            if approved:
                event.pop("normalized_decline_reason", None)
                event.pop("provider_raw_code", None)
            else:
                event["normalized_decline_reason"] = "timeout"
                event["provider_raw_code"] = "provider_timeout"
            events.append(event)
    return events


def prepare_store() -> dict[str, Any]:
    anchor = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    prepared = prepare_history.prepare(
        DB_PATH,
        hours=8.0,
        per_merchant_per_minute=24,
        seed=20260830,
        as_of=anchor,
        keep=False,
        detect_after=True,
    )
    connection = store.connect(DB_PATH)
    try:
        events = staged_events(anchor)
        ingested = store.ingest_stream(
            connection,
            (("attempt", event) for event in events),
            batch_size=2000,
            source="fallback-staging",
        )
        if ingested["rejected"] or ingested["accepted"] != len(events):
            raise RuntimeError(f"fallback: staged ingest was not clean: {ingested}")
        sweep = cli._sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        rows = store.list_incidents(connection)
    finally:
        connection.close()

    watches = [row for row in rows if row.get("lifecycle_state") == store.WATCHING]
    incidents = [
        row
        for row in rows
        if row.get("lifecycle_state") not in {store.WATCHING, "resolved", "mitigated"}
    ]
    if sweep.get("incident") is not None or len(watches) != 1 or incidents:
        raise RuntimeError(
            "fallback: detector did not produce exactly one watch and no incident: "
            f"incident={sweep.get('incident') is not None} watches={len(watches)} "
            f"other_rows={len(incidents)}"
        )
    if any(not value for value in sweep["watches"][0]["detection"]["watch"]["watch_floors"].values()):
        raise RuntimeError("fallback: persisted watch does not satisfy every watch floor")

    incident_id = str(watches[0]["incident_id"])
    print(f"fallback: running the real investigation for prepared watch {incident_id}", flush=True)
    investigated = subprocess.run(
        [
            sys.executable,
            "-m",
            "investigation.vertical",
            "--investigate-only",
            "--db",
            str(DB_PATH),
            "--incident-id",
            incident_id,
        ],
        cwd=ROOT,
        env={**os.environ, "CLEARWAVE_DB": str(DB_PATH)},
        check=False,
        text=True,
        capture_output=True,
        timeout=600,
    )
    sys.stdout.write(investigated.stdout)
    sys.stderr.write(investigated.stderr)
    if investigated.returncode:
        raise RuntimeError(
            f"fallback: prepared watch investigation exited {investigated.returncode}"
        )
    connection = store.connect(DB_PATH)
    try:
        rows = store.list_incidents(connection)
    finally:
        connection.close()
    watches = [row for row in rows if row.get("lifecycle_state") == store.WATCHING]
    if len(watches) != 1:
        raise RuntimeError(
            "fallback: investigation did not return the prepared record to watching: "
            f"{[(row.get('incident_id'), row.get('lifecycle_state')) for row in rows]}"
        )
    return {
        "anchor": anchor.isoformat(),
        "history": prepared["ingest"],
        "staged": ingested,
        "watch": watches[0],
    }


def wait_for_starting_state(timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{SURFACES_PORT}/api/overview"
    last_error = "dashboard did not answer"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                overview = json.load(response)
            if overview.get("active_incident_count") == 0 and len(overview.get("watches") or []) == 1:
                return
            last_error = (
                f"active_incident_count={overview.get('active_incident_count')} "
                f"watches={len(overview.get('watches') or [])}"
            )
        except (OSError, urllib.error.URLError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"fallback: starting state check failed: {last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reset and prepare the store, but do not start compose",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="start the isolated stack without rebuilding images",
    )
    args = parser.parse_args(argv)

    reset_stack()
    result = prepare_store()
    watch = result["watch"]
    print(
        "fallback: detector persisted watch "
        f"{watch['incident_id']} for {watch['affected_cohort']} "
        f"(staged {result['staged']['accepted']} canonical attempts)",
        flush=True,
    )
    if args.dry_run:
        print("fallback: dry run complete; compose was not started", flush=True)
        return 0

    up = ["up", "-d", "--wait", "--wait-timeout", "420"]
    if not args.no_build:
        up.insert(2, "--build")
    up.extend(SERVICES)
    print("fallback: starting isolated stack", flush=True)
    run_compose(*up, timeout=720)
    wait_for_starting_state()
    print(f"fallback: ready at http://127.0.0.1:{SURFACES_PORT}/", flush=True)
    print(f"fallback: store {DB_PATH} project {PROJECT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
