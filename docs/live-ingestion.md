# Live ingestion - W1's topics into the detection plane

W1 publishes to three Kafka topics. W2 consumes them into one SQLite store, and every
measurement, detection, localisation, price and severity already in the repository then runs on
that store. This document is how a person runs it, and what it does and does not do.

Owner: W2 (`andres`). The contract for what a record becomes is
[C1b canonical event](contracts/canonical-event.md); the implementation is `detector/consumer.py`.

## The two paths, deliberately independent

| Path | Command | Needs |
|---|---|---|
| **Live** | `python3 -m detector consume --detect` | Kafka, Schema Registry, a running W1 worker |
| **Offline** | `python3 -m detector seed && python3 -m detector detect` | nothing but Python |

The offline path is the demo fallback and does not import a Kafka client at any point. If the
broker will not start on the morning, it is still a complete demonstration of everything the
detection plane claims. Neither path is a special case of the other in the code: they are two front
doors onto the same normalisation, the same store and the same detection sweep.

## Running it end to end from a clean checkout

```sh
# 1. Broker, Schema Registry and raul's three merchants (raul's stack).
docker compose up -d kafka schema-registry \
  worker-merchant-a worker-merchant-b worker-merchant-c

# 2. The venv, which is where confluent-kafka lands. Only the consumer needs
#    it; nothing else in W2 does.
make install

# 3. Optional: W1's replayable history, streamed rather than held in memory.
export CLEARWAVE_DB=state/clearwave.db
python3 -m detector ingest /path/to/backfill.jsonl --stream

# 4. Consume for a minute, then detect - one command, live traffic to a C3 record.
.venv/bin/python -m detector consume --seconds 60 --detect
```

`make e2e` is all four steps in one command (`make e2e BACKFILL=/path/to/backfill.jsonl`
to include step 3). `make live` is step 4 alone, `make backfill BACKFILL=...` step 3 alone, and
`make consume` reads until the topics go quiet and stops.

The workers run *healthy* traffic until something injects an incident into them. Two ways to do
that, and neither restarts anything:

- **The dashboard.** `make surfaces-serve`, open http://127.0.0.1:8080, and use the judge toggle
  in the masthead. On publishes a provider-scoped decline; off publishes the stop command. The
  target it fires is named in `surfaces/inject.py` and returned in the API response.
- **The command line.** `python3 -m worker.inject merchant-b --provider adyen --effect decline`,
  and `python3 -m worker.inject merchant-b --stop` to clear it.

Both publish the same command to the same topic, because the dashboard calls `worker.inject`
rather than reimplementing it. There is also `python3 -m worker.worker merchant-a --scenario ...`
(see `worker/cli.py`) for starting a worker that is already running one of the named scenarios,
but that is a start-up choice, not something you can toggle mid-demo.

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
