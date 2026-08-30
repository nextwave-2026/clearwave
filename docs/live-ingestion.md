# Live ingestion - W1's topics into the detection plane

W1 publishes to three Kafka topics. W2 consumes them into one SQLite store, and every
measurement, detection, localisation, price and severity already in the repository then runs on
that store. This document is how a person runs it, and what it does and does not do.

Owner: W2 (`andres`). The contract for what a record becomes is
[C1b canonical event](contracts/canonical-event.md); the implementation is `detector/consumer.py`.

## The two paths, deliberately independent

| Path | Command | Needs |
|---|---|---|
| **Live, as a service** | `docker compose up -d detector` | Kafka, Schema Registry, a running W1 worker |
| **Live, as a command** | `.venv/bin/python -m detector consume --detect` | the same, plus somebody at the keyboard |
| **Offline** | `.venv/bin/python -m detector seed && .venv/bin/python -m detector detect` | the project virtualenv |

The offline path is the demo fallback and does not import a Kafka client at any point. If the
broker will not start on the morning, it is still a complete demonstration of everything the
detection plane claims. Neither path is a special case of the other in the code: they are two front
doors onto the same normalisation, the same store and the same detection sweep.

## Running it end to end from a clean checkout

The copy-pasteable demo sequence, including the offline fallback that actually produces a diagnosed incident on the dashboard, is [`docs/demo-sequence.md`](demo-sequence.md). This page is the live consumer. Several commands that used to be written here fail; do not use them.

```sh
# 0. Dependencies. Do not use system pip; it fails with PEP 668 on Homebrew Python.
make install

# 1. Broker, Schema Registry and raul's three merchants. Use the docker compose
#    subcommand; standalone docker-compose is not required. Wait for health.
docker compose up -d kafka schema-registry \
  worker-merchant-a worker-merchant-b worker-merchant-c

# 2. Optional: W1's replayable history, streamed rather than held in memory.
export CLEARWAVE_DB=state/clearwave.db
.venv/bin/python -m detector ingest /path/to/backfill.jsonl --stream

# 3. Consume for a minute, then detect. This is live traffic into the store.
#    It is not a guaranteed C3 record: healthy 60s traffic returns incident null.
#    Inject first (below) if you want an incident to detect.
.venv/bin/python -m detector consume --seconds 60 --detect
```

`make e2e` is steps 1 and 3 in one command, and `make e2e BACKFILL=/path/to/backfill.jsonl`
includes step 2. `make backfill BACKFILL=...` is step 2 alone; `make live` is step 3 alone -
it starts neither Kafka nor a worker and does not guarantee an incident. `make consume` reads
until the topics go quiet and stops. All of these run on `.venv`, which is what `make install`
populates; only `seed` and `detect` stay on system `python3`, because the broker-free fallback
needs nothing but the standard library.

Running a worker on the host instead of in the container works too, with two traps:
`PYTHONUNBUFFERED=1` is required (the image sets it, a host command does not - an empty log is not
a dead worker), and `--mode anomaly` does not exist.

```sh
PYTHONUNBUFFERED=1 .venv/bin/python -m worker.worker merchant-a --interval-seconds 0.2
```

## Running it as a service, which is how the demo runs it

The demo is a product, not a terminal session (`DECISIONS.md`, derek 2026-08-30T05:10Z): nobody
types anything. `docker compose up -d` brings up the `detector` service alongside the workers and
the investigation daemon, and detection then runs continuously with no operator at all.

```sh
docker compose up -d          # broker, registry, three merchants, detector, investigation
docker compose logs -f detector
```

It is the same consume loop the commands above run, with two differences and no third:

- **Empty polls are not terminal.** `detector consume` ends after three consecutive quiet polls,
  which is right for a bounded run and wrong for a service. The daemon passes `--idle-polls 0`,
  so only a signal ends it.
- **SIGINT and SIGTERM drain.** Both set a stop flag rather than raising, the loop leaves at the
  top of an iteration, and the batch already polled is written and only *then* acknowledged.
  `docker compose stop` therefore loses nothing, and a container that is killed replays its last
  uncommitted batch on restart - safe because every event table is keyed on `event_id`.

A broker failure the client cannot recover from exits the process, and `restart: unless-stopped`
brings it back. There is deliberately no retry policy inside the loop: compose already owns
process restart, and a second one here would be a supervisor to keep honest. Message-level errors
are unchanged - they are dead-lettered with their reason and the loop continues.

The service starts at each topic's beginning, not at its end, so a store that starts empty rebuilds
from whatever the broker still retains. It does **not** load W1's backfill file: that file is 83 MB,
is not in the repository and cannot be assumed present in a container. `make backfill BACKFILL=...`
remains the operator's path for it. What actually clears the six-hour floor under merchant-relative
severity is leaving the stack warm, which is the whole reason this is a service.

`make detect-daemon` runs the identical loop on the host, for anyone who wants it outside Docker:

```sh
make detect-daemon                                   # Ctrl-C to stop, it drains
make detect-daemon DB=state/clearwave.db DETECT_EVERY=15
```

The `detector` service is given `./state:/data` for the shared store, and an empty tmpfs over
`/data/ground_truth`. That mask is not decoration: `state/ground_truth/` lives inside `state/`, so
the mount that gives us the store would otherwise give us raul's hidden truth, which
`docs/ownership.md` quarantines from W2. `tests/test_detector_daemon.py` fails if the mask goes.

## Injecting an incident

The workers run *healthy* traffic until something injects an incident into them. Two ways, and
neither restarts anything:

- **The dashboard.** `make surfaces-serve`, open http://127.0.0.1:8080, and use the judge toggle in
  the masthead. On publishes a provider-scoped decline; off publishes the stop command. The target
  it fires is named in `surfaces/inject.py` and returned in the API response.
- **The command line.** `.venv/bin/python -m worker.inject merchant-b --provider adyen --effect
  decline`, and `.venv/bin/python -m worker.inject merchant-b --stop` to clear it.

Both publish the same command to the same topic, because the dashboard calls `worker.inject` rather
than reimplementing it. **Inject only after the target worker is publishing.** Injecting first is
silently lost: `incidents.control` starts from latest with a new consumer group, so a command sent
before the worker subscribes is never seen.

There is also `python3 -m worker.worker <merchant> --scenario ...` (see `worker/cli.py`) for
starting a worker that is already running one of the named scenarios, but that is a start-up
choice, not something you can toggle mid-demo.

The consumer prints what it did. This is a real run, three merchants live, taken while the judge
toggle was on:

```json
{
  "consumed": {"accepted": 1836, "duplicates": 0, "rejected": 0, "batches": 10,
               "polled": 1836, "by_topic": {"ops.telemetry": 159, "payments.attempts": 870,
                                            "payments.closed": 807}},
  "stored": {"attempt": 102642, "closed": 2350, "telemetry": 469},
  "detection": {"incident": {"...": "a C3 record with money attached"}, "stored": true}
}
```

Anything the consumer refused is in the store with its reason, not lost:

```sh
sqlite3 state/clearwave.db "SELECT source, reason, COUNT(*) FROM dead_letter GROUP BY 1, 2"
```

Everything downstream - the C2 evidence tools, W3's investigation, W4's dashboard - reads the same
file, located by `CLEARWAVE_DB`. Point them at it and they read the numbers the detector reports.

## Loading a backfill

W1 can hand over a replayable history: one `clearwave.attempt.v1` per line, JSON Lines. The plain
`ingest` reads a file whole, which is right for the fixtures and wrong for a backfill - 100,000
lines is 83 MB of text plus 100,000 parsed dicts held at once. `--stream` reads a line at a time
and writes in batches through the same `write_batch` the consumer uses, so there is one insert
path, one dead-letter rule and one dedupe rule, not two.

Measured on the 15-day, 100,000-event backfill, same store, same accepted counts either way:

| Path | Peak RSS | Wall clock |
|---|---|---|
| `ingest backfill.jsonl` | 572 MB | 3.8 s |
| `ingest backfill.jsonl --stream` | 29 MB | 6.4 s |

The streaming path trades a little wall clock for a working set that does not grow with the file.
A line it cannot parse is dead-lettered with its `path:line` and the rest of the file still loads;
the non-streaming reader refuses the whole file instead, which is the right answer for something
small enough to fix by hand and the wrong one for 100,000 lines.

## What the consumer guarantees

**Duplicates are counted once.** W1's schema declares `event_id` globally unique and says W2 dedupes
on it. Every event table is keyed on `event_id` and every insert is `INSERT OR IGNORE`, so a
redelivered record is stored once and reported as a duplicate rather than counted twice.

**Offsets advance only over durable writes.** A batch is written, the SQLite transaction is
committed, and only then are Kafka offsets stored and committed synchronously. A crash therefore
replays a batch rather than losing one, which is safe precisely because a replay cannot
double-count. `enable.auto.commit` is off; nothing else advances an offset.

**Event time, never arrival time.** Buckets, windows, onset and `as_of` come from `attempt_ts`,
`sample_ts` and `closed_ts`. The consumer reads no clock on the record path, so replaying a recorded
stream reproduces the live run exactly, and the same events arriving in any order produce an
identical incident.

**A rejection is visible.** An unrecognised shape, currency or decline reason - or an undecodable
payload, or a topic we do not consume - lands in `dead_letter` with its reason and `source='kafka'`.
There is no path by which a record is silently dropped, because a wrong count that nobody can see is
worse than a missing one.

## The three topics

| Topic | Table | What it is for |
|---|---|---|
| `payments.attempts` | `attempt` | everything detection measures |
| `ops.telemetry` | `telemetry_sample` | service-level runtime health, which attempts cannot carry |
| `payments.closed` | `payment_closed` | observed terminality, persisted but not yet measured |

`ops.telemetry` is what finally gives `operational_metrics.runtime_health` a real source. With no
sample it still answers `unobserved` with its reason, exactly as before; with samples it reports the
gauges W1 publishes and the criterion behind the verdict. `service_health` is unaffected and stays
derived from first-party attempts.

`payments.closed` is consumed and stored but deliberately feeds no measurement in this increment -
see the decision recorded in `DECISIONS.md`. Payment outcomes are already derived from attempts, and
two sources for one number at code freeze is the divergent-answer failure this architecture exists
to prevent. Persisting it now means the signal is in the store for whoever needs it, and replay
keeps it.

## The wire format

W1 publishes through the Schema Registry JSON serializer, which prefixes each value with a magic
byte and a schema reference. The consumer reads that frame itself rather than going through the
registry deserializer, so ingestion keeps working when the registry is down and the offline tests can
build the exact bytes W1 sends. Validation is not lost: every record still goes through C1b, which
is the check that actually decides whether something may be counted.
`tests/test_consumer.py` pins the framing constants against the client library's own whenever the
library is installed.

## Testing without a broker

`detector/consumer.py` takes a `Source`: `poll`, `commit`, `close`. `KafkaSource` is one
implementation and imports `confluent_kafka` lazily inside its constructor; `ReplaySource` is
another, over a list. `tests/test_consumer.py` drives the whole loop - routing, deduplication,
dead-lettering, commit ordering, bounds and the live pipeline through to a priced C3 record -
entirely offline. No CI job needs a broker, and none should be made to.
