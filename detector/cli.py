"""Operator entry point: get events in, then detect.

    python3 -m detector seed                      # deterministic demo traffic
    python3 -m detector ingest events.json        # a file of canonical events
    python3 -m detector ingest backfill.jsonl --stream   # a backfill too big to hold
    python3 -m detector consume                   # W1's live Kafka topics
    python3 -m detector daemon                    # the same, run as a service
    python3 -m detector detect                    # one detection sweep

`consume` is the live path and `seed`/`ingest` the offline one. They are two
front doors onto the same normalisation, the same store and the same detection,
which is why the demo has a fallback that needs no broker at all.

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
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import config, consumer, daemon, detect, evidence, metrics, schema, store

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


def _stream_jsonl(path: Path) -> Iterator[tuple[str, Any]]:
    """Yield one ``(kind, raw)`` per JSON Lines record, holding one line at a time.

    The point of the whole streaming path: `_load_events` reads the file into a
    string and then into a list, which is fine for a fixture and wrong for a
    15-day backfill. This never holds more than a line plus the caller's batch.

    A line that is not JSON is handed on as ``unroutable`` rather than aborting
    the load. `_load_events` refuses the file instead, which is the right call
    for a small file somebody can fix by hand; over 100,000 lines it would throw
    away every good record for one bad one, and the store's rule is that a
    rejection stays visible in `dead_letter` with its reason.
    """
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield ("attempt", json.loads(line))
            except json.JSONDecodeError as exc:
                yield ("unroutable", {"reason": f"{path}:{number}: not valid JSON ({exc.msg})"})


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


def adopt_watch_identity(connection, record: dict[str, Any], identify) -> str:
    """Keep the first-watch id when the picture sharpens or onset walks.

    The hash of onset-plus-cohort is the right identifier for a brand-new row.
    It is the wrong identifier for the next sweep of the same episode: onset
    drifts as the trailing window eats the incident, and localisation deepens
    as the drop grows, so hashing again mints a second row. A watching row
    whose cohort is this one, or a sharpening of it, is the same episode -
    pin the original id and the original onset so the warning and the incident
    are one record a judge can point at.
    """
    cohort = record.get("affected_cohort") or {}
    # Reuse any live episode, not only a watch. Once the row has been
    # detected, a later sweep with a walked onset used to hash a second id
    # because the match looked only at `watching`. Resolved rows are a prior
    # episode and must not be reused.
    live_states = {
        store.WATCHING,
        "detected",
        "investigating",
        "diagnosed",
        "acknowledged",
    }
    matches = [
        existing
        for existing in store.list_incidents(connection)
        if existing.get("lifecycle_state") in live_states
        and detect.cohorts_same_episode(existing.get("affected_cohort"), cohort)
    ]
    if not matches:
        return identify(record)
    matches.sort(key=lambda item: (item.get("onset") or "", item.get("incident_id") or ""))
    chosen = matches[0]
    record["onset"] = chosen["onset"]
    return chosen["incident_id"]


def _periodic_sweeper(connection, every_seconds: float, sink: list[dict[str, Any]], clock=time.monotonic):
    """A `consumer.consume` batch hook that sweeps on a wall-clock interval.

    A watch on a developing deviation is only worth anything while it is
    developing. Sweeping once at the end of a run means the earliest thing the
    operator ever sees is the cliff, which is the opposite of the point.

    It reuses the consumer's existing `on_batch` hook rather than adding a
    thread or a scheduler: the consume loop is already calling us after every
    durable batch, so the only thing missing was a clock. Returns None when the
    interval is off, which keeps the hook out of the loop entirely.
    """
    if every_seconds <= 0:
        return None
    next_at = clock() + every_seconds

    def on_batch(_progress) -> None:
        nonlocal next_at
        now = clock()
        if now < next_at:
            return
        next_at = now + every_seconds
        result = _sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
        sink.append(result)
        # Headlines to stderr, data to stdout: the run's stdout stays one JSON
        # document that a script can still parse, while a person watching the
        # demo sees the watch appear at the moment it appears.
        incident = result.get("incident")
        if incident is not None:
            print(
                f"detect: incident {incident['incident_id']} "
                f"{metrics.cohort_key(incident['affected_cohort'])} "
                f"severity={incident['severity']}",
                file=sys.stderr, flush=True,
            )
        for watch in result.get("watches") or ():
            print(
                f"detect: WATCHING {metrics.cohort_key(watch['affected_cohort'])} "
                f"- {', '.join(watch['detection']['watch']['reasons'])}, "
                f"projected "
                f"{watch['financial_impact']['projected_loss_per_hour']['amount']} "
                f"{watch['financial_impact']['projected_loss_per_hour']['currency']}/hour "
                "if it continues",
                file=sys.stderr, flush=True,
            )
        if incident is None and not result.get("watches"):
            print("detect: nothing above the floors and nothing watched", file=sys.stderr, flush=True)

    return on_batch


def _sweep(connection, window_buckets: int, persist: bool) -> dict[str, Any]:
    """One detection sweep over the stored window, in the shape the CLI prints.

    Shared by `detect` and by `consume --detect` so that live traffic and a
    replayed file reach detection through exactly the same call.
    """
    bounds = store.window_bounds(connection)
    if bounds is None:
        return {"incident": None, "reason": "no events stored"}
    # The sweep measures the trailing detection window behind the lateness
    # watermark, not the whole store. A degradation in the last few minutes is
    # invisible when it is averaged against every hour that preceded it.
    end = evidence.watermark(connection) + config.BUCKET_SECONDS
    start = bounds[0]
    if window_buckets > 0:
        start = max(start, end - window_buckets * config.BUCKET_SECONDS)
    # One aggregate for the whole sweep rather than one per candidate cohort.
    merchant_normals = detect.merchant_normal_hourly_value(connection)
    incident = detect.build_incident(connection, start, end, merchant_normals=merchant_normals)
    stored = False
    held_watch: dict[str, Any] | None = None
    if incident is not None:
        # Residual collapse heat in the 5-minute window must not keep the board
        # red after Clear. Judge the recent tail on the merchant/provider core,
        # not a thin bank/country child: residual heat on one issuer still looks
        # live after the parent provider has recovered, and that re-minted rows
        # after Clear. A thin tail is not recovery evidence - keep the full
        # window reading then.
        # One minute, not two: after Clear the verifier waits ~120s, and a
        # two-minute tail still contains the final collapse minute, so residual
        # heat kept qualifying and reminting. Collapse traffic is dense enough
        # that a live one-minute tail still clears the floors.
        core = _core_cohort(incident.get("affected_cohort") or {})
        # Empty platform cohort: residual-tail suppression is unsafe until
        # localise has named merchant/provider. Do not erase a platform hit here.
        if core:
            tail_start = max(start, end - 1 * config.BUCKET_SECONDS)
            tail = detect.evaluate(
                connection,
                core,
                tail_start,
                end,
            )
            tail_floors = tail.get("floors") or {}
            tail_volume_ok = bool(tail_floors.get("volume_min"))
            if tail_volume_ok and not tail.get("qualifies"):
                incident = None
    if incident is not None:
        incident["incident_id"] = adopt_watch_identity(
            connection, incident, incident_id_for
        )
        # After Clear, residual full-window heat can mint a fresh id once the
        # diagnosed row has resolved (resolved is not live for adopt). Refuse
        # that remint when the core tail has recovered - same bar as the
        # residual suppress above, applied to recently resolved same-core rows.
        if _resolved_core_still_recovered(connection, incident, start, end):
            incident = None
    if incident is not None:
        # Hysteresis on watch→detected: a mild developing inject often sits at
        # z just past Z_MIN. Upgrading immediately makes stage one sample a
        # detected row. Keep the existing watch until z is clearly past the
        # floor; collapse (0.95) clears this bar easily.
        existing = store.load_incident(connection, incident["incident_id"])
        z = (incident.get("detection") or {}).get("z")
        # Barely past Z_MIN stays a watch whether or not a prior watch row
        # exists - otherwise a first sweep that crosses at z~-3.1 never spends
        # time as watching and stage one fails.
        existing_watching = (
            existing is not None
            and str(existing.get("lifecycle_state") or "") == store.WATCHING
        )
        # Hold band is wide enough that a measured 0.12 developing inject on
        # merchant-b/adyen (z around -4.5 to -5) still spends time as watching.
        # Collapse at 0.95 is far past this bar. Detection floors are unchanged.
        barely_past = z is not None and z > -(float(config.Z_MIN) + 2.25)
        hold_watch = bool(barely_past and (existing_watching or existing is None))
        if hold_watch:
            # Refresh the watch in place with current evidence; do not hand off.
            held = dict(incident)
            held["lifecycle_state"] = store.WATCHING
            held["severity"] = "low"
            if persist:
                store.save_incident(
                    connection, held, lifecycle_state=store.WATCHING
                )
            held_watch = held
            incident = None
        elif persist:
            # lifecycle_state 'detected' is the sole handoff signal to L4
            # (DECISIONS.md, 2026-08-29T19:43Z), so detection writes it durably
            # rather than calling investigation. Adopting a watch id first is
            # what makes that write an upgrade of the warning, not a second row.
            stored = store.save_incident(connection, incident)
    # Watches are C3 records in `lifecycle_state: watching` on the same table,
    # written through the same identifier rule, so the row a cohort is watched
    # on is the row it is later detected on.
    formed = (incident or held_watch or {}).get("affected_cohort")
    watches = detect.build_watches(
        connection,
        start,
        end,
        formed_cohort=formed,
        merchant_normals=merchant_normals,
        identify=incident_id_for,
    )
    if held_watch is not None:
        # Prefer the held upgraded-evidence watch over a freshly built twin.
        watches = [
            watch
            for watch in watches
            if watch.get("incident_id") != held_watch.get("incident_id")
        ]
        watches.insert(0, held_watch)
    keep_ids: set[str] = set()
    if incident is not None:
        keep_ids.add(incident["incident_id"])
    if held_watch is not None and held_watch.get("incident_id"):
        keep_ids.add(str(held_watch["incident_id"]))
    if persist:
        for watch in watches:
            watch["incident_id"] = adopt_watch_identity(
                connection, watch, incident_id_for
            )
            store.save_incident(connection, watch, lifecycle_state=store.WATCHING)
            keep_ids.add(watch["incident_id"])
        store.expire_watches_except(connection, keep_ids)
        # When the inject is cleared, conversion recovers. Diagnosed rows must
        # resolve even if this sweep still builds an incident from residual
        # collapse traffic in the trailing window - keep_ids alone would skip
        # them and Clear would never return the board to healthy. Re-measure
        # every recoverable row; keep_ids only protects watches we just wrote.
        watch_keep = {str(w.get("incident_id")) for w in watches if w.get("incident_id")}
        if held_watch is not None and held_watch.get("incident_id"):
            watch_keep.add(str(held_watch["incident_id"]))
        # A detected row written this sweep is protected for one cycle so the
        # same sweep cannot create-then-resolve it (fixtures and live collapse
        # both hit that race when the tail is still thin). Exception: after
        # Clear a residual remint on a just-resolved same core must not ride
        # that protection past the verifier's 120s wait - recover it now.
        if stored and incident is not None and incident.get("incident_id"):
            if not _has_resolved_same_core(connection, incident):
                watch_keep.add(str(incident["incident_id"]))
        recovered = _recovered_incident_ids(connection, start, end, watch_keep)
        store.resolve_recovered_incidents(connection, recovered)
    else:
        for watch in watches:
            watch["incident_id"] = adopt_watch_identity(
                connection, watch, incident_id_for
            )

    return {
        "incident": incident,
        "stored": stored,
        "watches": watches,
        "window": {"start": schema.iso_utc(start), "end": schema.iso_utc(end)},
        "config_version": config.CONFIG_VERSION,
    }


def _recovered_incident_ids(
    connection,
    start: int,
    end: int,
    keep_ids: set[str],
) -> set[str]:
    """Open incident ids whose cohort is no longer degraded in this window.

    A row still being written this sweep (the current incident or a watch) is
    kept. Everything else in a recoverable state is re-measured: if the
    absolute drop has fallen below the watch floor and the z-score is no
    longer a near-miss, the traffic has recovered and the row should resolve.
    """
    recovered: set[str] = set()
    for row in store.list_incidents(connection):
        incident_id = row.get("incident_id")
        if not incident_id or incident_id in keep_ids:
            continue
        state = str(row.get("lifecycle_state") or "")
        if state not in store.RECOVERABLE_STATES:
            continue
        if state == store.WATCHING:
            # Watches are owned by expire_watches_except against this sweep's
            # keep set. Auto-resolving them here raced with collapse and split
            # one cohort into multiple ids.
            continue
        cohort = row.get("affected_cohort") or {}
        # Prefer the recent tail on the merchant/provider core: after Clear the
        # 5-minute window still holds collapse traffic, and a thin bank/country
        # child can still look hot after the parent recovered. A thin tail is
        # not recovery evidence on its own - fall back to the full window so a
        # sparse healthy period still clears a row once residual heat has left.
        core = _core_cohort(cohort or {})
        tail_start = max(start, end - 1 * config.BUCKET_SECONDS)
        tail = detect.evaluate(connection, core, tail_start, end)
        tail_floors = tail.get("floors") or {}
        if bool(tail_floors.get("volume_min")):
            if not tail.get("qualifies"):
                recovered.add(str(incident_id))
            continue
        full = detect.evaluate(connection, core, start, end)
        if not full.get("qualifies"):
            recovered.add(str(incident_id))
    return recovered


def _core_cohort(cohort: dict) -> dict | None:
    """Merchant/provider identity for residual-recovery checks.

    Bank, country, scheme, and method children are the same traffic with
    noisier tails. After Clear, parent recovery is what returns the board to
    healthy; a leftover issuer slice must not keep or remint the row.
    """
    core = {}
    if not isinstance(cohort, dict):
        return None
    if "merchant_id" in cohort:
        core["merchant_id"] = cohort["merchant_id"]
    if "provider" in cohort:
        core["provider"] = cohort["provider"]
    if core:
        return core
    return dict(cohort) if cohort else None


def _has_resolved_same_core(connection, incident: dict) -> bool:
    """True when a resolved row already names this merchant/provider core."""
    core = _core_cohort(incident.get("affected_cohort") or {})
    if not core:
        return False
    for existing in store.list_incidents(connection):
        if str(existing.get("lifecycle_state") or "") != store.RESOLVED:
            continue
        if _core_cohort(existing.get("affected_cohort") or {}) == core:
            return True
    return False


def _resolved_core_still_recovered(connection, incident: dict, start: int, end: int) -> bool:
    """True when a resolved same-core episode's live tail has recovered.

    Blocks residual heat from minting a second active row after Clear resolves
    the first. Live collapse still has a hot core tail, so it is not blocked.
    """
    core = _core_cohort(incident.get("affected_cohort") or {})
    if not core:
        return False
    resolved_match = False
    for existing in store.list_incidents(connection):
        if str(existing.get("lifecycle_state") or "") != store.RESOLVED:
            continue
        if _core_cohort(existing.get("affected_cohort") or {}) != core:
            continue
        resolved_match = True
        break
    if not resolved_match:
        return False
    tail_start = max(start, end - 1 * config.BUCKET_SECONDS)
    tail = detect.evaluate(connection, core, tail_start, end)
    tail_floors = tail.get("floors") or {}
    if not bool(tail_floors.get("volume_min")):
        # Thin tail after resolve: fall back to full window.
        full = detect.evaluate(connection, core, start, end)
        return not full.get("qualifies")
    return not tail.get("qualifies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="detector", description=__doc__)
    parser.add_argument("--db", default=None, help="SQLite store path (default: $CLEARWAVE_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="normalise and store canonical events")
    ingest.add_argument("source", type=Path, help="JSON file of canonical events")
    ingest.add_argument(
        "--stream", action="store_true",
        help="read the file as JSON Lines one line at a time and write in "
             "batches, instead of loading it whole. Use it for anything the "
             "size of a backfill; the fixtures are small enough not to care.",
    )
    ingest.add_argument(
        "--batch-size", type=int, default=1000,
        help="records written per transaction when --stream is set",
    )

    seed = sub.add_parser("seed", help="load deterministic synthetic events into the store")
    seed.add_argument("--scenario", choices=SCENARIOS, default="provider_incident")

    consume_command = sub.add_parser(
        "consume", help="read W1's live Kafka topics into the store, then optionally detect"
    )
    consume_command.add_argument(
        "--bootstrap-servers",
        default=None,
        help=f"Kafka bootstrap servers (default: ${consumer.BOOTSTRAP_ENV_VAR} "
             f"or {consumer.DEFAULT_BOOTSTRAP})",
    )
    consume_command.add_argument(
        "--group-id", default=consumer.DEFAULT_GROUP_ID,
        help="consumer group; the same group resumes where it left off",
    )
    consume_command.add_argument(
        "--topics", nargs="+", default=sorted(consumer.TOPICS),
        choices=sorted(consumer.TOPICS),
        help="which of W1's topics to read (default: all three)",
    )
    consume_command.add_argument(
        "--from-latest", action="store_true",
        help="start at the end of each topic instead of replaying it from the beginning",
    )
    consume_command.add_argument(
        "--seconds", type=float, default=None,
        help="stop after this many seconds; omit to stop when the topics go quiet",
    )
    consume_command.add_argument(
        "--max-messages", type=int, default=None, help="stop after this many messages",
    )
    consume_command.add_argument(
        "--batch-size", type=int, default=200,
        help="records written per transaction before offsets advance",
    )
    consume_command.add_argument(
        "--idle-polls", type=int, default=3,
        help="consecutive empty polls that end the run; 0 never ends on idle, "
             "which is what `detector daemon` uses",
    )
    consume_command.add_argument(
        "--detect", action="store_true",
        help="run a detection sweep on what was consumed, so one command goes "
             "from live traffic to a stored C3 record",
    )
    consume_command.add_argument(
        "--detect-every", type=float, default=0.0, metavar="SECONDS",
        help="also sweep every SECONDS while consuming, instead of only at the "
             "end. A watch on a developing deviation is only useful if it "
             "appears while the deviation is developing; without this a live "
             "run shows nothing until the cliff has already happened. Each "
             "sweep persists, and its headline goes to stderr as it happens.",
    )

    daemon_command = sub.add_parser(
        "daemon",
        help="consume and sweep continuously until SIGINT or SIGTERM (the compose service)",
    )
    daemon.add_arguments(daemon_command)

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

    if args.command == "daemon":
        # The daemon owns its own connection for the whole process lifetime, so
        # it is dispatched before the one-shot commands open theirs.
        try:
            return daemon.run(args)
        except ValueError as exc:
            print(f"detector daemon: {exc}", flush=True)
            return 2

    connection = store.connect(args.db or store.database_path())
    try:
        if args.command == "ingest":
            if args.stream:
                summary = store.ingest_stream(
                    connection, _stream_jsonl(args.source), batch_size=args.batch_size
                )
            else:
                summary = store.ingest(connection, _load_events(args.source))
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        if args.command == "seed":
            summary = store.ingest(connection, _scenario_events(args.scenario))
            print(json.dumps({"scenario": args.scenario, **summary}, indent=2, sort_keys=True))
            return 0

        if args.command == "consume":
            source = consumer.KafkaSource(
                bootstrap_servers=args.bootstrap_servers,
                group_id=args.group_id,
                topics=tuple(args.topics),
                from_beginning=not args.from_latest,
            )
            deadline = None if args.seconds is None else time.monotonic() + args.seconds
            sweeps: list[dict[str, Any]] = []
            try:
                progress = consumer.consume(
                    connection,
                    source,
                    batch_size=args.batch_size,
                    idle_polls=args.idle_polls,
                    max_messages=args.max_messages,
                    deadline=deadline,
                    on_batch=_periodic_sweeper(connection, args.detect_every, sweeps),
                )
            except KeyboardInterrupt:
                # Whatever the last completed batch wrote is already durable and
                # already acknowledged. Stopping here loses nothing and duplicates
                # nothing.
                progress = None
            finally:
                source.close()
            summary = {
                "consumed": (progress.as_dict() if progress is not None else "interrupted"),
                "stored": store.stored_counts(connection),
            }
            if sweeps:
                summary["periodic_detection"] = sweeps
            if args.detect:
                summary["detection"] = _sweep(connection, config.DETECT_WINDOW_BUCKETS, persist=True)
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0

        print(json.dumps(
            _sweep(connection, args.window_buckets, persist=not args.no_persist),
            indent=2,
            sort_keys=True,
        ))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
