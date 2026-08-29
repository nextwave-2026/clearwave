"""Operator entry point: ingest canonical events, then detect.

    python3 -m detector seed                      # deterministic demo traffic
    python3 -m detector ingest events.json
    python3 -m detector detect

Every subcommand reads and writes one SQLite file. It is located by the
``CLEARWAVE_DB`` environment variable, defaulting to ``state/clearwave.db``
relative to the working directory, which is the same file the C2 evidence
tools read. Setting the variable once points a whole demo at one store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import config, detect, evidence, metrics, schema, store

SCENARIOS = ("healthy", "provider_incident", "confounded")


def _load_events(path: Path) -> list[dict[str, Any]]:
    """Read events from a JSON array, an ``{"events": [...]}`` envelope, JSON
    Lines, or a single event object.

    All four turn up in practice: a batch export, the vertical slice's fixture,
    a stream dump, and a single hand-written sample. Refusing any of them just
    makes somebody reformat a file to prove a point.
    """
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        events = []
        for number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{number}: not valid JSON ({exc.msg})") from exc
        if events:
            return events
        raise SystemExit(f"{path}: not valid JSON and not JSON Lines")

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("events"), list):
            return payload["events"]
        return [payload]
    raise SystemExit(f"{path}: expected a JSON object, an array, or an events envelope")


def _scenario_events(scenario: str) -> list[dict[str, Any]]:
    """The repository's own deterministic generator, never a second one.

    ``tests/synthetic.py`` already produces seeded canonical events, so seeding
    a store for a demo or a manual tool call reuses it rather than growing a
    parallel simulation nobody reconciles.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from tests import synthetic
    except ImportError as exc:  # pragma: no cover - a partial checkout
        raise SystemExit(f"the synthetic event generator is unavailable: {exc}") from exc
    if scenario == "healthy":
        return synthetic.healthy()
    if scenario == "confounded":
        return synthetic.confounded()
    return synthetic.with_provider_incident()


def incident_id_for(incident: dict[str, Any]) -> str:
    """A replay-stable identifier: the same events always name the same incident.

    Derived from the onset and the affected cohort rather than from a counter
    or a clock, so a re-run cites the identifier a judge already has on screen.
    """
    cohort_key = metrics.cohort_key(incident.get("affected_cohort") or {})
    digest = hashlib.sha256(f"{incident['onset']}|{cohort_key}".encode("utf-8")).hexdigest()
    return f"inc-{incident['onset'][:10]}-{digest[:8]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="detector", description=__doc__)
    parser.add_argument("--db", default=None, help="SQLite store path (default: $CLEARWAVE_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="normalise and store canonical events")
    ingest.add_argument("source", type=Path, help="JSON file of canonical events")

    seed = sub.add_parser("seed", help="load deterministic synthetic events into the store")
    seed.add_argument("--scenario", choices=SCENARIOS, default="provider_incident")

    detect_command = sub.add_parser("detect", help="run one detection sweep over the stored window")
    detect_command.add_argument(
        "--window-buckets",
        type=int,
        default=config.DETECT_WINDOW_BUCKETS,
        help="how many trailing buckets the sweep measures (0 sweeps the whole store)",
    )
    detect_command.add_argument(
        "--no-persist",
        action="store_true",
        help="print the incident without writing it to the store",
    )

    args = parser.parse_args(argv)
    connection = store.connect(args.db or store.database_path())

    if args.command == "ingest":
        summary = store.ingest(connection, _load_events(args.source))
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.command == "seed":
        summary = store.ingest(connection, _scenario_events(args.scenario))
        print(json.dumps({"scenario": args.scenario, **summary}, indent=2, sort_keys=True))
        return 0

    bounds = store.window_bounds(connection)
    if bounds is None:
        print(json.dumps({"incident": None, "reason": "no events stored"}, indent=2))
        return 0
    # The sweep measures the trailing detection window behind the lateness
    # watermark, not the whole store. A degradation in the last few minutes is
    # invisible when it is averaged against every hour that preceded it.
    end = evidence.watermark(connection) + config.BUCKET_SECONDS
    start = bounds[0]
    if args.window_buckets > 0:
        start = max(start, end - args.window_buckets * config.BUCKET_SECONDS)
    incident = detect.build_incident(connection, start, end)
    stored = False
    if incident is not None:
        incident["incident_id"] = incident_id_for(incident)
        if not args.no_persist:
            # lifecycle_state 'detected' is the sole handoff signal to L4
            # (DECISIONS.md, 2026-08-29T19:43Z), so detection writes it durably
            # rather than calling investigation.
            stored = store.save_incident(connection, incident)
    print(json.dumps(
        {
            "incident": incident,
            "stored": stored,
            "window": {"start": schema.iso_utc(start), "end": schema.iso_utc(end)},
            "config_version": config.CONFIG_VERSION,
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
