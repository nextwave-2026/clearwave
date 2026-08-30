"""Write healthy live-vocabulary history into the demo store.

A four-minute judge session cannot wait for six hours of traffic to accumulate.
This fills the store with merchant-shaped, event-time history immediately
behind now, then stops. It never writes an incident or a watch: those still
have to come from the detector looking at live traffic after the judge presses
the button.

Default is a clean start. A store that still holds last rehearsal's incidents
would promote a later band (PR #67). Re-running this command replaces the
file; `--keep` is the explicit opt-in to append.

    python3 -S scripts/prepare_history.py
    python3 -S scripts/prepare_history.py --db /tmp/warm.db --hours 8
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector import config, detect, evidence, store  # noqa: E402
from tests.synthetic import (  # noqa: E402
    DEMO_MERCHANT_ID,
    DEMO_PROVIDER,
    LIVE_HISTORY_HOURS,
    LIVE_HISTORY_PER_MERCHANT_PER_MINUTE,
    LIVE_HISTORY_SEED,
    iter_live_healthy_history,
)

DEFAULT_DB = store.DEFAULT_DB


def parse_as_of(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def unlink_store(path: Path) -> None:
    """Remove the SQLite file and its WAL sidecars so the next open is empty."""
    if str(path) == ":memory:":
        return
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists() or candidate.is_symlink():
            candidate.unlink()


def cohort_warmth(connection: sqlite3.Connection) -> dict[str, object]:
    """Detection-baseline warmth for the demo cohort, merchant-relative separately."""
    needed_buckets = config.BASELINE_TRAILING_BUCKETS
    needed_seconds = needed_buckets * config.BUCKET_SECONDS
    row = connection.execute(
        """
        SELECT COUNT(*) AS attempts,
               COUNT(DISTINCT occurred_epoch / ?) AS buckets,
               MIN(occurred_epoch) AS lo,
               MAX(occurred_epoch) AS hi
        FROM attempt
        WHERE merchant_id = ? AND provider = ?
        """,
        (config.BUCKET_SECONDS, DEMO_MERCHANT_ID, DEMO_PROVIDER),
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    buckets = int(row["buckets"] or 0)
    lo = row["lo"]
    hi = row["hi"]
    span = (int(hi) - int(lo)) if lo is not None and hi is not None else 0
    baseline_warm = attempts > 0 and (buckets >= needed_buckets or span >= needed_seconds)

    merchant_row = connection.execute(
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
        (DEMO_MERCHANT_ID,),
    ).fetchone()
    payments = int(merchant_row["payments"] or 0)
    mlo = merchant_row["lo"]
    mhi = merchant_row["hi"]
    hours = ((int(mhi) - int(mlo)) / 3600.0) if payments and mlo is not None and mhi is not None else 0.0
    normals = detect.merchant_normal_hourly_value(connection)
    merchant_warm = DEMO_MERCHANT_ID in normals
    try:
        incidents = connection.execute("SELECT COUNT(*) AS n FROM incident").fetchone()["n"]
    except sqlite3.Error:
        incidents = 0
    return {
        "demo_merchant": DEMO_MERCHANT_ID,
        "demo_provider": DEMO_PROVIDER,
        "cohort_attempts": attempts,
        "cohort_buckets": buckets,
        "cohort_span_seconds": span,
        "baseline_needed_buckets": needed_buckets,
        "baseline_warm": baseline_warm,
        "merchant_payments": payments,
        "merchant_hours": hours,
        "merchant_needed_hours": config.MERCHANT_NORMAL_MIN_HOURS,
        "merchant_needed_payments": config.MERCHANT_NORMAL_MIN_PAYMENTS,
        "merchant_warm": merchant_warm,
        "merchant_normal_hourly_value_usd": normals.get(DEMO_MERCHANT_ID),
        "incident_rows": int(incidents or 0),
    }


def warmth_lines(report: dict[str, object], missing: str | None = None) -> list[str]:
    if missing:
        return [
            f"history: detection baseline cold ({missing})",
            f"history: merchant-relative cold ({missing})",
        ]
    baseline = "warm" if report["baseline_warm"] else "cold"
    merchant = "warm" if report["merchant_warm"] else "cold"
    hours = float(report["merchant_hours"])
    normal = report["merchant_normal_hourly_value_usd"]
    normal_text = "null" if normal is None else f"{float(normal):.2f} USD/hour"
    return [
        (
            f"history: detection baseline {baseline} "
            f"({report['demo_merchant']}/{report['demo_provider']} "
            f"{report['cohort_buckets']} buckets / {report['cohort_attempts']} attempts, "
            f"need {report['baseline_needed_buckets']})"
        ),
        (
            f"history: merchant-relative {merchant} "
            f"({report['demo_merchant']} {hours:.2f}h / {report['merchant_payments']} payments, "
            f"need {report['merchant_needed_hours']}h / {report['merchant_needed_payments']}; "
            f"merchant_normal_hourly_value_usd={normal_text})"
        ),
    ]


def sweep_without_persisting(connection: sqlite3.Connection) -> dict[str, object]:
    """The real detection sweep, with persistence off so history stays silent."""
    bounds = store.window_bounds(connection)
    if bounds is None:
        return {"incident": None, "watches": [], "reason": "no events stored"}
    end = evidence.watermark(connection) + config.BUCKET_SECONDS
    start = max(bounds[0], end - config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS)
    merchant_normals = detect.merchant_normal_hourly_value(connection)
    incident = detect.build_incident(
        connection, start, end, merchant_normals=merchant_normals
    )
    watches = detect.build_watches(
        connection,
        start,
        end,
        formed_cohort=(incident or {}).get("affected_cohort"),
        merchant_normals=merchant_normals,
    )
    return {"incident": incident, "watches": watches}


def prepare(
    path: Path,
    *,
    hours: float,
    per_merchant_per_minute: int,
    seed: int,
    as_of: datetime | None,
    keep: bool,
    detect_after: bool,
) -> dict[str, object]:
    started = time.perf_counter()
    if not keep:
        unlink_store(path)
    connection = store.connect(path)
    try:
        events = (
            ("attempt", event)
            for event in iter_live_healthy_history(
                hours=hours,
                per_merchant_per_minute=per_merchant_per_minute,
                seed=seed,
                as_of=as_of,
            )
        )
        ingest = store.ingest_stream(connection, events, batch_size=2000, source="prepare")
        report = cohort_warmth(connection)
        detection: dict[str, object] | None = None
        if detect_after:
            detection = sweep_without_persisting(connection)
            # Re-read in case a future caller turns persistence on by mistake.
            report = cohort_warmth(connection)
    finally:
        connection.close()
    elapsed = time.perf_counter() - started
    return {
        "db": str(path),
        "hours": hours,
        "seed": seed,
        "clean_start": not keep,
        "elapsed_seconds": elapsed,
        "ingest": ingest,
        "warmth": report,
        "detection": detection,
    }


def render(result: dict[str, object]) -> int:
    ingest = result["ingest"]
    elapsed = float(result["elapsed_seconds"])
    print(
        f"prepare: wrote {ingest['accepted']} attempts "
        f"({ingest['rejected']} rejected, {ingest['duplicates']} duplicates) "
        f"in {elapsed:.2f}s to {result['db']}"
    )
    print(f"prepare: stored {ingest['stored']} attempt rows, clean_start={result['clean_start']}")
    for line in warmth_lines(result["warmth"]):
        print(f"prepare: {line}")
    detection = result.get("detection")
    if not detection:
        return 0
    incident = detection.get("incident")
    watches = detection.get("watches") or ()
    incident_id = None if incident is None else incident.get("incident_id")
    print(
        f"prepare: detection sweep incident={incident_id!s} "
        f"watches={len(watches)} incident_rows={result['warmth']['incident_rows']}"
    )
    if incident is not None or watches or int(result["warmth"]["incident_rows"] or 0):
        print(
            "prepare: refusing to start from a store that already has a warning. "
            "History must be healthy; the detector has to find the live deviation itself.",
            file=sys.stderr,
        )
        return 1
    if ingest["rejected"]:
        print("prepare: some events were rejected; not a warm store.", file=sys.stderr)
        return 1
    if not result["warmth"]["baseline_warm"] or not result["warmth"]["merchant_warm"]:
        print("prepare: store is not warm enough for the demo floors.", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prepare_history", description=__doc__)
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite store path (default: $CLEARWAVE_DB or state/clearwave.db)",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=LIVE_HISTORY_HOURS,
        help=(
            f"event-time hours to write immediately behind now "
            f"(default {LIVE_HISTORY_HOURS}; must clear MERCHANT_NORMAL_MIN_HOURS="
            f"{config.MERCHANT_NORMAL_MIN_HOURS})"
        ),
    )
    parser.add_argument(
        "--per-minute",
        type=int,
        default=LIVE_HISTORY_PER_MERCHANT_PER_MINUTE,
        dest="per_merchant_per_minute",
        help=f"payments per merchant per minute (default {LIVE_HISTORY_PER_MERCHANT_PER_MINUTE})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=LIVE_HISTORY_SEED,
        help=f"RNG seed so a rehearsal matches the real run (default {LIVE_HISTORY_SEED})",
    )
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        dest="as_of",
        help="anchor timestamp (RFC 3339). Default: now, floored to a minute",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="append into the existing store instead of replacing it (not the demo default)",
    )
    parser.add_argument(
        "--skip-detect",
        action="store_true",
        help="do not sweep after writing; warmth is still reported",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.db) if args.db else store.database_path()
    result = prepare(
        path,
        hours=args.hours,
        per_merchant_per_minute=args.per_merchant_per_minute,
        seed=args.seed,
        as_of=args.as_of,
        keep=args.keep,
        detect_after=not args.skip_detect,
    )
    return render(result)


if __name__ == "__main__":
    raise SystemExit(main())
