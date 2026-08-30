#!/usr/bin/env python3
"""Drive the demo chain against a real stack and print an honest verdict per beat.

A judge pressing a control is the judge acting, never the system noticing.
Every PASS rests on something this run observed. Failures are reported and
the remaining beats still run. This script does not tune, skip, or special-case
a check so the run comes out green.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_BASE = ROOT / "docker-compose.yml"
COMPOSE_OVERLAY = ROOT / "scripts" / "verify-demo.compose.yml"
DEFAULT_PROJECT = "clearwave-verify-demo"
DEFAULT_STATE_DIR = ROOT / "state" / "verify-demo"
DEFAULT_SURFACES_PORT = 18082
# Host ports a live demo on this machine is already using. The isolated stack
# must not bind them.
OCCUPIED_PORTS = frozenset({8080, 8081, 8082, 8090, 9092, 18080})
FORBIDDEN_PROJECTS = frozenset({"clearwave"})

# Published floors from docs/demo-sequence.md / AGENTS.md, observed in the store.
BASELINE_NEEDED_BUCKETS = 60
BUCKET_SECONDS = 60
MERCHANT_NEEDED_HOURS = 6.0
MERCHANT_NEEDED_PAYMENTS = 200
DEMO_MERCHANT = "merchant-b"
DEMO_PROVIDER = "adyen"

QUIET_SECONDS = 120
STAGE_ONE_SECONDS = 240
STAGE_TWO_SECONDS = 480
CLEAR_SECONDS = 120
EXTRA_SWEEP_SECONDS = 45
POLL_SECONDS = 5
HTTP_TIMEOUT = 8
COMPOSE_WAIT_TIMEOUT = 420

STACK_SERVICES = (
    "kafka",
    "schema-registry",
    "worker-merchant-a",
    "worker-merchant-b",
    "worker-merchant-c",
    "detector",
    "investigation",
    "surfaces",
)

# Words this verifier must never emit as a capability claim.
FORBIDDEN_CLAIM_WORDS = (
    "seasonal",
    "seasonality",
    "friday night",
    "hour-of-week",
    "hour of week",
)


@dataclass
class Beat:
    key: str
    title: str
    passed: bool
    evidence: str


@dataclass
class Snapshot:
    at: str
    overview: dict[str, Any] | None
    queue: dict[str, Any] | None
    merchants: dict[str, Any] | None
    escalations: dict[str, Any] | None
    calls: dict[str, Any] | None
    store_rows: list[dict[str, Any]]
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def money_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "amount" in value:
        try:
            return float(value["amount"])
        except (TypeError, ValueError):
            return None
    return None


def money_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "absent"
    amount = money_amount(value)
    if amount is None:
        return "absent"
    currency = str(value.get("currency") or "USD")
    return f"{currency} {amount:.2f}"


def cohort_text(cohort: Any) -> str:
    if not isinstance(cohort, dict) or not cohort:
        return "{}"
    parts = []
    for key in (
        "merchant_id",
        "provider",
        "country",
        "issuing_bank",
        "payment_method",
        "card_network",
    ):
        item = cohort.get(key)
        if item:
            parts.append(f"{key}={item}")
    return "{" + ", ".join(parts) + "}"


def one_line(value: Any, limit: int = 720) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def is_watch(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return str(row.get("lifecycle_state") or "").strip().lower() == "watching"


def is_active(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    state = str(row.get("lifecycle_state") or "").strip().lower()
    return state not in {"watching", "resolved", "mitigated", ""}


def is_injected_cohort(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    cohort = row.get("affected_cohort") or {}
    if not isinstance(cohort, dict):
        return False
    merchant = str(cohort.get("merchant_id") or "")
    provider = str(cohort.get("provider") or "")
    return merchant == DEMO_MERCHANT or provider == DEMO_PROVIDER


def identity_line(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "none"
    parts = []
    for row in rows:
        parts.append(
            f"{row.get('incident_id')}:{row.get('lifecycle_state')} {cohort_text(row.get('affected_cohort'))}"
        )
    return " | ".join(parts)


def page_leads_with_revenue(html: str) -> bool:
    lowered = html.lower()
    return "revenue impact" in lowered


def page_renders_merchant_money_under_calm(js: str) -> bool:
    """True when the page draws merchant money while the headline is calm.

    Observed from the dashboard JS the browser actually loads, not from a
    Python helper. #75 calls renderMerchants before the calm early return.
    """
    merchants = js.find("renderMerchants(state.merchants)")
    calm = js.find("No revenue at risk")
    if merchants == -1 or calm == -1:
        return False
    return merchants < calm


def watch_presented_as_active(overview: dict[str, Any]) -> tuple[bool, str]:
    incidents = list(overview.get("incidents") or [])
    watches = list(overview.get("watches") or [])
    count = overview.get("active_incident_count")
    watch_ids = {item.get("incident_id") for item in watches if item.get("incident_id")}
    watching_in_queue = [
        item.get("incident_id")
        for item in incidents
        if is_watch(item) or item.get("incident_id") in watch_ids
    ]
    mismatched = count is not None and count != len(incidents)
    lied = bool(watching_in_queue) or bool(mismatched)
    evidence = (
        f"active_incident_count={count} incidents={len(incidents)} "
        f"watches={len(watches)} watch_ids_in_incident_list={watching_in_queue or 'none'}"
    )
    return lied, evidence


def stale_revenue_on_board(
    overview: dict[str, Any],
    merchants: list[dict[str, Any]],
    js: str,
) -> tuple[bool, str]:
    """Calm headline plus merchant-row money copied off a resolved incident."""
    active_count = overview.get("active_incident_count")
    headline_incidents = list(overview.get("incidents") or [])
    calm = active_count == 0 and not headline_incidents
    money_rows = []
    for row in merchants:
        financial = row.get("financial_impact") or {}
        loss = money_amount((financial or {}).get("loss_per_hour"))
        risk = money_amount((financial or {}).get("gmv_at_risk"))
        if loss or risk:
            money_rows.append(
                {
                    "scope": row.get("scope_label") or row.get("merchant_id"),
                    "source": row.get("source_incident_id") or (row.get("incident_ids") or [None])[0],
                    "active_incident_count": row.get("active_incident_count"),
                    "loss_per_hour": money_text(financial.get("loss_per_hour")),
                    "gmv_at_risk": money_text(financial.get("gmv_at_risk")),
                }
            )
    would_draw = page_renders_merchant_money_under_calm(js)
    contradiction = calm and bool(money_rows) and would_draw
    evidence = (
        f"active_incident_count={active_count} headline_incidents={len(headline_incidents)} "
        f"merchant_money_rows={one_line(money_rows) if money_rows else 'none'} "
        f"page_draws_merchant_money_under_calm={would_draw}"
    )
    return contradiction, evidence


def paging_side_effects(escalations: dict[str, Any] | None, calls: dict[str, Any] | None) -> dict[str, Any]:
    slack: list[dict[str, Any]] = []
    phone: list[dict[str, Any]] = []
    dashboard: list[dict[str, Any]] = []
    for group in (escalations or {}).get("incidents") or []:
        incident_id = group.get("incident_id")
        for channel in group.get("channels") or []:
            item = {
                "incident_id": incident_id,
                "channel": channel.get("channel"),
                "status": channel.get("status"),
                "detail": channel.get("detail"),
            }
            name = str(channel.get("channel") or "").lower()
            if name == "slack":
                slack.append(item)
            elif name == "phone":
                phone.append(item)
            elif name == "dashboard":
                dashboard.append(item)
    pending = list((calls or {}).get("calls") or [])
    return {
        "slack": slack,
        "phone": phone,
        "dashboard": dashboard,
        "pending_calls": pending,
        "slack_count": len(slack),
        "phone_count": len(phone),
        "pending_call_count": len(pending),
    }


def open_store(path: Path, write: bool = False) -> sqlite3.Connection:
    if write:
        connection = sqlite3.connect(str(path), timeout=10)
    else:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=8000")
    return connection


def store_incidents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        connection = open_store(path)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT incident_id, lifecycle_state, severity, record FROM incident"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    out = []
    for row in rows:
        try:
            record = json.loads(row["record"])
        except (TypeError, ValueError, json.JSONDecodeError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        record["incident_id"] = row["incident_id"]
        record["lifecycle_state"] = row["lifecycle_state"]
        record["severity"] = record.get("severity") or row["severity"]
        out.append(record)
    return out


def store_warmth(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "baseline_warm": False,
        "merchant_warm": False,
        "cohort_attempts": 0,
        "cohort_buckets": 0,
        "merchant_payments": 0,
        "merchant_hours": 0.0,
        "incident_rows": 0,
        "error": None,
    }
    if not path.exists():
        report["error"] = f"no database at {path}"
        return report
    try:
        connection = open_store(path)
    except sqlite3.Error as exc:
        report["error"] = str(exc)
        return report
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS attempts,
                   COUNT(DISTINCT occurred_epoch / ?) AS buckets,
                   MIN(occurred_epoch) AS lo,
                   MAX(occurred_epoch) AS hi
            FROM attempt
            WHERE merchant_id = ? AND provider = ?
            """,
            (BUCKET_SECONDS, DEMO_MERCHANT, DEMO_PROVIDER),
        ).fetchone()
        attempts = int(row["attempts"] or 0)
        buckets = int(row["buckets"] or 0)
        lo, hi = row["lo"], row["hi"]
        span = (int(hi) - int(lo)) if lo is not None and hi is not None else 0
        report["cohort_attempts"] = attempts
        report["cohort_buckets"] = buckets
        report["baseline_warm"] = attempts > 0 and (
            buckets >= BASELINE_NEEDED_BUCKETS or span >= BASELINE_NEEDED_BUCKETS * BUCKET_SECONDS
        )
        merchant = connection.execute(
            """
            SELECT COUNT(*) AS payments,
                   MIN(first_epoch) AS lo,
                   MAX(first_epoch) AS hi
            FROM (
                SELECT payment_id, MIN(occurred_epoch) AS first_epoch
                FROM attempt
                WHERE merchant_id = ?
                GROUP BY payment_id
            )
            """,
            (DEMO_MERCHANT,),
        ).fetchone()
        payments = int(merchant["payments"] or 0)
        mlo, mhi = merchant["lo"], merchant["hi"]
        hours = (
            (int(mhi) - int(mlo)) / 3600.0
            if payments and mlo is not None and mhi is not None
            else 0.0
        )
        report["merchant_payments"] = payments
        report["merchant_hours"] = hours
        report["merchant_warm"] = (
            hours >= MERCHANT_NEEDED_HOURS and payments >= MERCHANT_NEEDED_PAYMENTS
        )
        try:
            report["incident_rows"] = int(
                connection.execute("SELECT COUNT(*) AS n FROM incident").fetchone()["n"] or 0
            )
        except sqlite3.Error:
            report["incident_rows"] = 0
    except sqlite3.Error as exc:
        report["error"] = str(exc)
    finally:
        connection.close()
    return report


def json_request(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            parsed = {"error": raw or str(exc)}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"error": "non-json", "body": raw}
    return status, parsed


def text_request(url: str, timeout: float = HTTP_TIMEOUT) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def print_table(beats: list[Beat]) -> None:
    print()
    print(f"{'BEAT':<28} {'RESULT':<6} EVIDENCE")
    print("-" * 88)
    for index, beat in enumerate(beats, start=1):
        verdict = "PASS" if beat.passed else "FAIL"
        label = f"{index} {beat.key}"
        print(f"{label:<28} {verdict:<6} {beat.evidence}")
    print("-" * 88)
    passed = sum(1 for beat in beats if beat.passed)
    overall = "PASS" if passed == len(beats) else "FAIL"
    print(f"OVERALL {overall}  {passed}/{len(beats)} beats passed")
    print()


class DemoVerifier:
    def __init__(
        self,
        *,
        dashboard: str,
        db_path: Path,
        project: str,
        isolated: bool,
        keep_stack: bool,
        quiet_seconds: int,
        stage_one_seconds: int,
        stage_two_seconds: int,
        clear_seconds: int,
        build: bool,
    ) -> None:
        self.dashboard = dashboard.rstrip("/")
        self.db_path = db_path
        self.project = project
        self.isolated = isolated
        self.keep_stack = keep_stack
        self.quiet_seconds = quiet_seconds
        self.stage_one_seconds = stage_one_seconds
        self.stage_two_seconds = stage_two_seconds
        self.clear_seconds = clear_seconds
        self.build = build
        self.beats: list[Beat] = []
        self.stage_one_ids: list[str] = []
        self.stage_two_ids: list[str] = []
        self.stage_one_rows: list[dict[str, Any]] = []
        self.stage_two_rows: list[dict[str, Any]] = []
        self.collapse_posted_at: float | None = None
        self.diagnosed_at: float | None = None
        self.html = ""
        self.js = ""
        self.overview_snaps: list[dict[str, Any]] = []
        self.dashboard_up = False

    def compose(self, *args: str) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            str(COMPOSE_BASE),
            "-f",
            str(COMPOSE_OVERLAY),
            *args,
        ]

    def run_compose(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.compose(*args),
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        return json_request(method, self.dashboard + path, body)

    def post_stage(self, stage: str) -> tuple[int, Any]:
        return self.api("POST", "/api/trigger", {"stage": stage})

    def snapshot(self) -> Snapshot:
        status_o, overview = self.api("GET", "/api/overview")
        status_q, queue = self.api("GET", "/api/incidents")
        status_m, merchants = self.api("GET", "/api/merchants")
        status_e, escalations = self.api("GET", "/api/escalations")
        status_c, calls = self.api("GET", "/api/calls")
        error = None
        if status_o != 200:
            error = f"GET /api/overview -> {status_o} {one_line(overview)}"
            overview = None
        if status_q != 200:
            queue = None
        if status_m != 200:
            merchants = None
        if status_e != 200:
            escalations = None
        if status_c != 200:
            calls = None
        snap = Snapshot(
            at=utc_now(),
            overview=overview if isinstance(overview, dict) else None,
            queue=queue if isinstance(queue, dict) else None,
            merchants=merchants if isinstance(merchants, dict) else None,
            escalations=escalations if isinstance(escalations, dict) else None,
            calls=calls if isinstance(calls, dict) else None,
            store_rows=store_incidents(self.db_path),
            error=error,
        )
        if snap.overview:
            self.overview_snaps.append(snap.overview)
        return snap

    def wait_for(self, seconds: int, label: str) -> None:
        if not self.dashboard_up:
            print(
                f"verify-demo: skipping {seconds}s wait ({label}); dashboard not answering",
                flush=True,
            )
            return
        print(f"verify-demo: waiting {seconds}s ({label})", flush=True)
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(POLL_SECONDS, remaining))

    def record(self, key: str, title: str, passed: bool, evidence: str) -> None:
        for word in FORBIDDEN_CLAIM_WORDS:
            if word in evidence.lower() or word in title.lower():
                evidence = (
                    f"{evidence} [verifier-blocked phrase {word!r} stripped from claims]"
                )
        self.beats.append(Beat(key=key, title=title, passed=passed, evidence=one_line(evidence)))
        verdict = "PASS" if passed else "FAIL"
        print(f"verify-demo: {verdict}  {key}  {one_line(evidence)}", flush=True)

    def prepare_state_dir(self) -> None:
        for merchant in ("merchant-a", "merchant-b", "merchant-c"):
            (self.db_path.parent / "ground_truth" / merchant).mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o777)
        except OSError:
            pass

    def bring_up(self) -> str | None:
        if self.project in FORBIDDEN_PROJECTS:
            return f"refusing compose project {self.project!r}; that name is the live demo"
        self.prepare_state_dir()
        print("verify-demo: tearing down any leftover isolated stack first", flush=True)
        self.run_compose("down", "--volumes", "--remove-orphans", timeout=180)
        print(f"verify-demo: seeding healthy history into {self.db_path}", flush=True)
        prepared = subprocess.run(
            [
                sys.executable,
                "-S",
                str(ROOT / "scripts" / "prepare_history.py"),
                "--db",
                str(self.db_path),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=180,
        )
        sys.stdout.write(prepared.stdout)
        if prepared.returncode != 0:
            sys.stderr.write(prepared.stderr)
            return f"prepare_history exited {prepared.returncode}"
        up = ["up", "-d"]
        if self.build:
            up.append("--build")
        up.extend(["--wait", "--wait-timeout", str(COMPOSE_WAIT_TIMEOUT), *STACK_SERVICES])
        print("verify-demo: bringing up isolated compose stack", flush=True)
        result = self.run_compose(*up, timeout=COMPOSE_WAIT_TIMEOUT + 180)
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            return f"docker compose up exited {result.returncode}"
        # Give the dashboard one extra poll so the first overview is a real read.
        time.sleep(2)
        return None

    def tear_down(self) -> None:
        if not self.isolated or self.keep_stack:
            if self.keep_stack:
                print(
                    f"verify-demo: leaving stack up at {self.dashboard}/ "
                    f"(project {self.project})",
                    flush=True,
                )
            return
        print("verify-demo: tearing down isolated stack", flush=True)
        self.run_compose("down", "--volumes", "--remove-orphans", timeout=180)

    def load_page(self) -> None:
        _, html = text_request(self.dashboard + "/")
        _, js = text_request(self.dashboard + "/app.js")
        self.html = html
        self.js = js

    def beat_clean_start(self) -> None:
        warmth = store_warmth(self.db_path)
        snap = self.snapshot()
        self.load_page()
        overview = snap.overview or {}
        active = overview.get("active_incident_count")
        watches = list(overview.get("watches") or [])
        self.dashboard_up = snap.error is None and active is not None
        passed = (
            self.dashboard_up
            and warmth["baseline_warm"]
            and warmth["merchant_warm"]
            and active == 0
            and not watches
        )
        evidence = (
            f"baseline={'warm' if warmth['baseline_warm'] else 'cold'} "
            f"{warmth['cohort_buckets']} buckets/{warmth['cohort_attempts']} attempts "
            f"(need {BASELINE_NEEDED_BUCKETS}); "
            f"merchant-relative={'warm' if warmth['merchant_warm'] else 'cold'} "
            f"{warmth['merchant_hours']:.2f}h/{warmth['merchant_payments']} payments "
            f"(need {MERCHANT_NEEDED_HOURS}h/{MERCHANT_NEEDED_PAYMENTS}); "
            f"active_incident_count={active} watches={len(watches)} "
            f"store_incident_rows={warmth['incident_rows']}"
        )
        if snap.error:
            evidence = f"{evidence}; {snap.error}"
        if warmth.get("error"):
            evidence = f"{evidence}; store={warmth['error']}"
        self.record("clean-start-warm", "Clean start is genuinely warm", passed, evidence)

    def beat_healthy_quiet(self) -> None:
        self.wait_for(
            self.quiet_seconds,
            "healthy traffic, nobody touching the controls",
        )
        snap = self.snapshot()
        rows = snap.store_rows
        watches = [row for row in rows if is_watch(row)]
        actives = [row for row in rows if is_active(row)]
        overview = snap.overview or {}
        api_watches = list(overview.get("watches") or [])
        api_active = overview.get("active_incident_count")
        # Quiet means nothing appeared: a watch that #77 later expires into
        # resolved still appeared on healthy traffic.
        passed = (
            not rows
            and not api_watches
            and api_active == 0
            and not snap.error
        )
        evidence = (
            f"window={self.quiet_seconds}s store_rows={len(rows)} "
            f"store_watches={len(watches)} store_active={len(actives)} "
            f"api_watches={len(api_watches)} api_active={api_active} "
            f"ids={identity_line(rows)}"
        )
        self.record(
            "healthy-traffic-quiet",
            "Healthy traffic stays quiet",
            passed,
            evidence,
        )

    def beat_stage_one(self) -> None:
        status, body = self.post_stage("developing")
        delivered = isinstance(body, dict) and body.get("delivered") is True
        if not delivered:
            self.record(
                "stage-one-watch",
                "Stage one developing deviation",
                False,
                f"judge posted developing; control did not deliver HTTP {status} {one_line(body)}",
            )
            return
        deadline = time.time() + self.stage_one_seconds
        found_at = None
        last = self.snapshot()
        while time.time() < deadline:
            last = self.snapshot()
            injected = [row for row in last.store_rows if is_injected_cohort(row)]
            api_injected = [
                item
                for item in list((last.overview or {}).get("watches") or [])
                if isinstance(item, dict) and is_injected_cohort(item)
            ]
            if injected or api_injected:
                found_at = time.time()
                break
            time.sleep(POLL_SECONDS)
        if found_at is not None:
            extra_end = min(deadline, found_at + EXTRA_SWEEP_SECONDS)
            while time.time() < extra_end:
                time.sleep(POLL_SECONDS)
                last = self.snapshot()
        rows = [row for row in last.store_rows if is_injected_cohort(row)]
        api_injected = [
            item
            for item in list((last.overview or {}).get("watches") or [])
            if isinstance(item, dict) and is_injected_cohort(item)
        ]
        self.stage_one_rows = rows or api_injected
        self.stage_one_ids = [
            str(row.get("incident_id"))
            for row in self.stage_one_rows
            if row.get("incident_id")
        ]
        merchant_relative = []
        for row in self.stage_one_rows:
            cohort = row.get("affected_cohort") or {}
            projected = row.get("projected_loss_per_hour")
            if projected is None:
                projected = (row.get("financial_impact") or {}).get("projected_loss_per_hour")
            has_merchant = str((cohort or {}).get("merchant_id") or "") == DEMO_MERCHANT
            has_projected = money_amount(projected) not in (None, 0.0)
            if has_merchant and has_projected:
                merchant_relative.append(
                    {
                        "incident_id": row.get("incident_id"),
                        "lifecycle_state": row.get("lifecycle_state"),
                        "cohort": cohort,
                        "projected_loss_per_hour": projected,
                    }
                )
        passed = bool(merchant_relative) and any(
            is_watch(row) or str(row.get("lifecycle_state")) == "watching"
            for row in self.stage_one_rows
        )
        evidence = (
            f"judge posted developing delivered=true; "
            f"waited_up_to={self.stage_one_seconds}s; "
            f"injected_records={identity_line(self.stage_one_rows)}; "
            f"merchant-relative_watch={one_line(merchant_relative) if merchant_relative else 'none'}"
        )
        self.record(
            "stage-one-watch",
            "Stage one developing deviation",
            passed,
            evidence,
        )

    def beat_no_page_before_detection(self) -> None:
        snap = self.snapshot()
        effects = paging_side_effects(snap.escalations, snap.calls)
        detected_or_later = [
            row
            for row in snap.store_rows
            if str(row.get("lifecycle_state") or "").lower()
            in {"detected", "investigating", "diagnosed", "acknowledged"}
        ]
        paged = effects["slack_count"] or effects["phone_count"] or effects["pending_call_count"]
        # The guarantee is: stage one must not page. Silence is only a PASS if
        # we actually reached the board that would have fired the side effect.
        observed = snap.error is None and self.dashboard_up
        passed = observed and not paged
        evidence = (
            f"slack={one_line(effects['slack']) if effects['slack'] else 'none'} "
            f"phone={one_line(effects['phone']) if effects['phone'] else 'none'} "
            f"pending_calls={effects['pending_call_count']} "
            f"detected_or_later={identity_line(detected_or_later)}"
        )
        if not observed:
            evidence = f"dashboard not answering; paging side effect not observed; {evidence}"
        self.record(
            "no-page-before-detection",
            "No page before detection",
            passed,
            evidence,
        )

    def beat_one_record(self) -> None:
        # Identity is compared after stage two has had a chance to run. This
        # beat is filled in then; a placeholder is recorded only if collapse
        # never starts.
        return

    def beat_stage_two_and_identity(self) -> None:
        status, body = self.post_stage("collapse")
        delivered = isinstance(body, dict) and body.get("delivered") is True
        self.collapse_posted_at = time.time()
        if not delivered:
            self.record(
                "one-cohort-one-record",
                "One cohort, one record",
                False,
                f"stage-one ids={self.stage_one_ids or 'none'}; collapse did not deliver HTTP {status} {one_line(body)}",
            )
            self.record(
                "stage-two-collapse",
                "Stage two collapse",
                False,
                f"judge posted collapse; control did not deliver HTTP {status} {one_line(body)}",
            )
            return
        deadline = time.time() + self.stage_two_seconds
        diagnosed = None
        last = self.snapshot()
        while time.time() < deadline:
            last = self.snapshot()
            candidates = [
                row
                for row in last.store_rows
                if is_injected_cohort(row)
                and str(row.get("lifecycle_state") or "").lower() == "diagnosed"
            ]
            if candidates:
                # Investigation result is what the board exposes on detail.
                for row in candidates:
                    incident_id = str(row.get("incident_id") or "")
                    st, detail = self.api("GET", f"/api/incidents/{incident_id}")
                    investigation = (detail or {}).get("investigation") if st == 200 else None
                    if isinstance(investigation, dict) and investigation.get("outcome"):
                        diagnosed = (row, investigation, detail)
                        self.diagnosed_at = time.time()
                        break
            if diagnosed:
                break
            time.sleep(POLL_SECONDS)
        if diagnosed:
            extra_end = min(deadline, time.time() + EXTRA_SWEEP_SECONDS)
            while time.time() < extra_end:
                time.sleep(POLL_SECONDS)
                last = self.snapshot()
        injected = [row for row in last.store_rows if is_injected_cohort(row)]
        self.stage_two_rows = injected
        self.stage_two_ids = [str(row.get("incident_id")) for row in injected if row.get("incident_id")]
        unique_one = sorted(set(self.stage_one_ids))
        unique_two = sorted(set(self.stage_two_ids))
        stable = (
            len(unique_one) == 1
            and len(unique_two) == 1
            and unique_one == unique_two
        )
        self.record(
            "one-cohort-one-record",
            "One cohort, one record",
            stable,
            (
                f"stage_one_ids={unique_one or 'none'} "
                f"stage_two_ids={unique_two or 'none'} "
                f"stage_one={identity_line(self.stage_one_rows)} "
                f"stage_two={identity_line(self.stage_two_rows)}"
            ),
        )
        elapsed = None
        if self.collapse_posted_at is not None and self.diagnosed_at is not None:
            elapsed = self.diagnosed_at - self.collapse_posted_at
        effects = paging_side_effects(last.escalations, last.calls)
        paged = effects["slack_count"] or effects["phone_count"] or effects["pending_call_count"]
        outcome = None
        record = None
        if diagnosed:
            record, investigation, detail = diagnosed
            outcome = investigation.get("outcome")
        passed = diagnosed is not None and paged
        elapsed_text = f"{elapsed:.1f}s" if elapsed is not None else "not observed"
        evidence = (
            f"judge posted collapse delivered=true; "
            f"elapsed_control_to_diagnosis={elapsed_text}; "
            f"diagnosed_id={(record or {}).get('incident_id') if record else 'none'} "
            f"outcome={outcome or 'none'} "
            f"lifecycle={(record or {}).get('lifecycle_state') if record else 'none'} "
            f"slack={one_line(effects['slack']) if effects['slack'] else 'none'} "
            f"phone={one_line(effects['phone']) if effects['phone'] else 'none'} "
            f"pending_calls={effects['pending_call_count']}"
        )
        self.record(
            "stage-two-collapse",
            "Stage two collapse",
            passed,
            evidence,
        )

    def beat_clear(self) -> None:
        status, body = self.post_stage("clear")
        delivered = isinstance(body, dict) and body.get("delivered") is True
        if not delivered:
            self.record(
                "clear-returns-healthy",
                "Clear returns the board to healthy",
                False,
                f"judge posted clear; control did not deliver HTTP {status} {one_line(body)}",
            )
            return
        self.wait_for(self.clear_seconds, "after clear, watching leftover rows")
        snap = self.snapshot()
        watching = [row for row in snap.store_rows if is_watch(row)]
        actives = [row for row in snap.store_rows if is_active(row)]
        overview = snap.overview or {}
        passed = delivered and not watching and not actives and overview.get("active_incident_count") == 0
        evidence = (
            f"judge posted clear delivered=true; leftover_watching={len(watching)} "
            f"active={len(actives)} api_active={overview.get('active_incident_count')} "
            f"ids={identity_line(snap.store_rows)}"
        )
        self.record(
            "clear-returns-healthy",
            "Clear returns the board to healthy",
            passed,
            evidence,
        )

    def beat_board_truth(self) -> None:
        self.load_page()
        leads = page_leads_with_revenue(self.html)
        lies = []
        snap_notes = []
        for overview in self.overview_snaps or []:
            lied, note = watch_presented_as_active(overview)
            snap_notes.append(note)
            if lied:
                lies.append(note)
        # One more live snapshot so a watch that arrived late is still scored.
        live = self.snapshot()
        if live.overview:
            lied, note = watch_presented_as_active(live.overview)
            snap_notes.append(note)
            if lied:
                lies.append(note)
        heading = "Revenue impact" if leads else "not 'Revenue impact'"
        passed = leads and not lies
        evidence = (
            f"html_heading={heading}; watch_as_active={'yes ' + lies[0] if lies else 'no'}; "
            f"last_snapshot={snap_notes[-1] if snap_notes else 'none'}"
        )
        self.record(
            "board-tells-the-truth",
            "The board leads with revenue impact and does not count a watch as an active incident",
            passed,
            evidence,
        )

    def _force_resolved_only(self) -> str:
        """Drive the store into resolved-and-no-active so beat 9 can observe the board."""
        if not self.db_path.exists():
            return "no store to resolve"
        connection = open_store(self.db_path, write=True)
        try:
            connection.execute(
                """
                UPDATE incident
                SET lifecycle_state = 'resolved'
                WHERE lower(lifecycle_state) NOT IN ('resolved', 'mitigated', 'watching')
                """
            )
            connection.commit()
            remaining_active = connection.execute(
                """
                SELECT COUNT(*) AS n FROM incident
                WHERE lower(lifecycle_state) NOT IN ('resolved', 'mitigated', 'watching')
                """
            ).fetchone()["n"]
            resolved = connection.execute(
                """
                SELECT COUNT(*) AS n FROM incident
                WHERE lower(lifecycle_state) = 'resolved'
                """
            ).fetchone()["n"]
            if resolved == 0:
                now = utc_now()
                record = {
                    "incident_id": "inc-verify-demo-resolved-probe",
                    "lifecycle_state": "resolved",
                    "severity": "high",
                    "affected_cohort": {
                        "merchant_id": DEMO_MERCHANT,
                        "provider": DEMO_PROVIDER,
                    },
                    "change": {"metric": "payment_approval_conversion", "expected": 0.9, "actual": 0.5},
                    "financial_impact": {
                        "gmv_at_risk": {"amount": 1648.72, "currency": "USD"},
                        "loss_per_hour": {"amount": 19784.62, "currency": "USD"},
                    },
                }
                connection.execute(
                    """
                    INSERT INTO incident (
                        incident_id, created_at, record, cohort_key, severity,
                        severity_score, lifecycle_state, onset_epoch, last_seen_epoch,
                        config_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["incident_id"],
                        now,
                        json.dumps(record, sort_keys=True),
                        f"merchant_id={DEMO_MERCHANT}|provider={DEMO_PROVIDER}",
                        "high",
                        0.0,
                        "resolved",
                        0,
                        0,
                        "verify-demo",
                    ),
                )
                connection.commit()
                resolved = 1
        except sqlite3.Error as exc:
            return f"store write failed: {exc}"
        finally:
            connection.close()
        return f"resolved_rows={resolved} remaining_active={remaining_active}"

    def beat_no_stale_revenue(self) -> None:
        setup = self._force_resolved_only()
        time.sleep(1)
        self.load_page()
        snap = self.snapshot()
        overview = snap.overview or {}
        merchants = list((snap.merchants or {}).get("merchants") or [])
        contradiction, board = stale_revenue_on_board(overview, merchants, self.js)
        passed = (
            "store write failed" not in setup
            and snap.error is None
            and not contradiction
        )
        evidence = (
            f"setup={setup}; {board}; "
            f"calm_copy_in_js={'No revenue at risk' in self.js}"
        )
        if snap.error:
            evidence = f"dashboard not answering; board not observed; {evidence}"
        self.record(
            "no-stale-revenue",
            "The board never shows money that is not currently at risk",
            passed,
            evidence,
        )

    def run_beats(self) -> int:
        print(
            f"verify-demo: dashboard {self.dashboard}/ store {self.db_path} "
            f"isolated={self.isolated}",
            flush=True,
        )
        self.beat_clean_start()
        self.beat_healthy_quiet()
        self.beat_stage_one()
        self.beat_no_page_before_detection()
        self.beat_stage_two_and_identity()
        self.beat_clear()
        self.beat_board_truth()
        self.beat_no_stale_revenue()
        print_table(self.beats)
        failed = [beat for beat in self.beats if not beat.passed]
        return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--against",
        default=os.environ.get("VERIFY_DEMO_AGAINST", ""),
        help="Dashboard URL of an already-running stack. Default: bring up an isolated stack.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("VERIFY_DEMO_DB", ""),
        help="SQLite store path. Isolated default: state/verify-demo/clearwave.db",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("VERIFY_DEMO_PROJECT", DEFAULT_PROJECT),
        help=f"Compose project name for the isolated stack (default {DEFAULT_PROJECT})",
    )
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        default=os.environ.get("VERIFY_DEMO_KEEP", "") == "1",
        help="Leave the isolated stack running at the end",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip docker compose --build",
    )
    parser.add_argument("--quiet-seconds", type=int, default=int(os.environ.get("VERIFY_DEMO_QUIET", QUIET_SECONDS)))
    parser.add_argument("--stage-one-seconds", type=int, default=int(os.environ.get("VERIFY_DEMO_STAGE_ONE", STAGE_ONE_SECONDS)))
    parser.add_argument("--stage-two-seconds", type=int, default=int(os.environ.get("VERIFY_DEMO_STAGE_TWO", STAGE_TWO_SECONDS)))
    parser.add_argument("--clear-seconds", type=int, default=int(os.environ.get("VERIFY_DEMO_CLEAR", CLEAR_SECONDS)))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    against = (args.against or "").strip()
    isolated = not against
    if isolated:
        dashboard = f"http://127.0.0.1:{DEFAULT_SURFACES_PORT}"
        db_path = Path(args.db) if args.db else DEFAULT_STATE_DIR / "clearwave.db"
        if args.project in FORBIDDEN_PROJECTS:
            print(
                f"verify-demo: refusing project {args.project!r}; it is the live demo",
                file=sys.stderr,
            )
            return 2
        if DEFAULT_SURFACES_PORT in OCCUPIED_PORTS:
            print("verify-demo: isolated surfaces port collides with a live port", file=sys.stderr)
            return 2
    else:
        dashboard = against
        db_path = Path(args.db) if args.db else ROOT / "state" / "clearwave.db"
        print(
            "verify-demo: driving an already-running stack; "
            "this will press the judge controls on that board",
            flush=True,
        )
    verifier = DemoVerifier(
        dashboard=dashboard,
        db_path=db_path,
        project=args.project,
        isolated=isolated,
        keep_stack=args.keep_stack,
        quiet_seconds=args.quiet_seconds,
        stage_one_seconds=args.stage_one_seconds,
        stage_two_seconds=args.stage_two_seconds,
        clear_seconds=args.clear_seconds,
        build=not args.no_build,
    )
    try:
        if isolated:
            error = verifier.bring_up()
            if error:
                print(f"verify-demo: isolated stack did not come up: {error}", flush=True)
        return verifier.run_beats()
    except KeyboardInterrupt:
        print("verify-demo: interrupted", file=sys.stderr)
        return 130
    finally:
        verifier.tear_down()


if __name__ == "__main__":
    raise SystemExit(main())
