"""Operator entry point: ingest canonical events, then detect.

    python3 -m detector ingest --db state/clearwave.db events.json
    python3 -m detector detect --db state/clearwave.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import config, detect, store


def _load_events(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return payload["events"]
    raise SystemExit("event file must be a JSON array or an object with an 'events' array")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="detector", description=__doc__)
    parser.add_argument("--db", default=str(store.DEFAULT_DB), help="SQLite store path")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="normalise and store canonical events")
    ingest.add_argument("source", type=Path, help="JSON file of canonical events")

    sub.add_parser("detect", help="run one detection sweep over the stored window")

    args = parser.parse_args(argv)
    connection = store.connect(args.db)

    if args.command == "ingest":
        summary = store.ingest(connection, _load_events(args.source))
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    bounds = store.window_bounds(connection)
    if bounds is None:
        print(json.dumps({"incident": None, "reason": "no events stored"}, indent=2))
        return 0
    start, end = bounds
    incident = detect.build_incident(connection, start, end + config.BUCKET_SECONDS)
    print(json.dumps({"incident": incident, "config_version": config.CONFIG_VERSION},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
