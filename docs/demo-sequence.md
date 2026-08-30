# Demo sequence

Orientation for a live Control Tower demo. This file is not a second product baseline.

- Product direction: [`docs/prd.md`](prd.md)
- Workstreams and seams: [`docs/ownership.md`](ownership.md)
- Settled calls: [`DECISIONS.md`](../DECISIONS.md)
- Current contract shapes: [`INTERFACES.md`](../INTERFACES.md)
- Frozen contracts: [`docs/contracts/`](contracts/)

If this file and the PRD disagree, the PRD governs. Correct the disagreement in `DECISIONS.md`, not here.

## Operator runbook - copy-paste this under pressure

Wrong commands fail on stage. This section is the path that was actually run. Use the **offline** sequence unless live Kafka is already up and healthy before the pitch starts.

### Which path to use

| Path | Standing |
| --- | --- |
| **Offline deterministic** (seed / detect / `investigation.vertical` / dashboard) | **Safe stage path.** Proven. This worktree: seed+detect stored a critical `provider-p2` incident; `investigation.vertical` diagnosed it; `GET /api/overview` showed `lifecycle_state: diagnosed` with a narrative. An earlier rehearsal timed all three guaranteed scenarios at 2 minutes 48 seconds. Model calls vary (about 45-100+ seconds). Do not promise three fresh model calls inside four minutes. |
| **Live containerised Kafka** | The worker -> Kafka -> detector hop works. The operator experience on this tree does **not** produce a judge-fired incident. Do not open a four-minute demo with this path. Commands below are from a live Docker run plus CLI checks here; this worktree did **not** re-run Docker. |

### 0. Once per machine

```sh
make install
```

Use `.venv/bin/python` for every command below.

Do **not** run `python3 -m pip install -r detector/requirements.txt` on the system interpreter. It fails with PEP 668 (`externally-managed-environment`) on Homebrew Python 3.14.

Do **not** run bare `python3 -m investigation.vertical`. It fails with `ModuleNotFoundError: No module named 'openai'` before it parses flags. The docstring in `investigation/vertical.py` still advertises that command; it is wrong on this machine.

`OPENAI_API_KEY` must be set for a model diagnosis. Copy `.env.example` to `.env` and fill the key. Do not print it. Without the key the incident still stores and the dashboard still renders; the narrative is `agent_unavailable`.

### 1. Stage path: cold checkout to a diagnosed incident on screen

Two terminals. Repository root. Same `$DB`.

This is the exact sequence run in this worktree. Port `18080` was used because `8080` / `8090` may already be taken on a shared machine. The product default is `8080` (`CLEARWAVE_SURFACES_PORT`). If you change `PORT`, use that value in the browser URL.

Terminal A - dashboard:

```sh
DB=/tmp/clearwave-demo.db
PORT=18080
rm -f "$DB" "$DB-wal" "$DB-shm"
CLEARWAVE_SURFACES_QUIET=1 .venv/bin/python -m surfaces.server \
  --host 127.0.0.1 --port "$PORT" --db "$DB"
```

Wait until the process is listening. Open `http://127.0.0.1:18080/`. An empty store shows zero incidents.

Terminal B - seed, detect, investigate:

```sh
DB=/tmp/clearwave-demo.db
.venv/bin/python -m investigation.vertical --db "$DB"
```

Wait for `Lifecycle after investigate: diagnosed`. The dashboard polls the same file. Select the incident. Stop Terminal A with Ctrl-C.

What this produced here:

- Incident cohort `{provider: provider-p2}`, conversion 0.849744 -> 0.52, severity `critical`, GMV at risk USD 1648.72, loss per hour USD 19784.62
- Investigation `outcome=ambiguous`, `diagnostic_confidence=medium`, `narrative_available=true`
- Dashboard `GET /api/overview`: `active_incident_count: 1`, `lifecycle_state: diagnosed`
- `POST /api/trigger` and `POST /api/judge/trigger` answer `wired: true`, but with no broker running they report `delivered: false` and say so - this path injects nothing and does not pretend to

This path is the broker-free default `detector seed` scenario (`provider_incident`). It is not Kafka, and it is not the judge clicking Fire hidden incident.

### Investigating a store that is already detected against

This is the join from detection to investigation, and it is a product command now. Use it whenever
the store was written by something other than `investigation.vertical` itself - a live Kafka
consume, the judge toggle, a store you preloaded before the pitch.

```sh
DB=/tmp/clearwave-live.db
.venv/bin/python -m investigation.vertical --db "$DB" --investigate-only
# or, to name the incident rather than take the newest detected one:
.venv/bin/python -m investigation.vertical --db "$DB" --incident-id inc-2026-08-30-715ab9c3
```

`make investigate DB=/tmp/clearwave-live.db` is the same thing, and takes an optional
`INCIDENT=inc-...`.

What it does and does not do:

- It **never seeds, never detects, and never resets the store.** It claims one incident that is
  already `lifecycle_state: detected`, runs the real investigation runner against it, persists C4,
  and moves that incident to `diagnosed`.
- With no `--incident-id` it takes the **newest detected** incident (`onset` descending). With one,
  it takes exactly that incident, or refuses by name and lists what is detected.
- A store with nothing detected is an error that says so and points at `detector detect`. It does
  not quietly seed one for you.
- A store path that does not exist is an error. Investigate-only never creates a store.
- `--keep` has nothing to do with this. `--keep` still seeds and detects; it only skips deleting the
  file first. `--investigate-only` is the flag that skips seed and detect.

The default `python3 -m investigation.vertical` behaviour is unchanged: seed, detect, investigate,
against a store it recreates. The proven stage path in section 1 above is exactly as it was.

### The other two guaranteed scenarios

`detector seed --scenario` only accepts `healthy`, `provider_incident`, `confounded`. It does not accept the catalogue names `provider-degradation`, `provider-issuer-confounded`, or `high-impact-small-percentage`. Those names exist on `python -m worker.worker --scenario` (live Kafka, and they require that scenario's own merchant: merchant-c, merchant-c, merchant-a).

The other two offline generators live in `tests.synthetic` and need hand-written ingest. That is a rehearsal workaround, not a supported operator command. If you need those two on screen, preload the stores before the pitch.

### Live Kafka path - what genuinely works, and what does not

The Kafka hop is real and the judge trigger now fires through it. A 60-second consume on *healthy* traffic still stores no incident, which is correct: you have to inject something first, and then give detection enough sustained buckets to see it.

Observed on a live Docker run: `docker compose up -d kafka schema-registry` brought the broker up; workers published; `.venv/bin/python -m detector consume --seconds 60 --detect` decoded Schema Registry frames and wrote the SQLite store (1319 accepted, 0 rejected, **incident null** on healthy traffic); `.venv/bin/python -m worker.inject merchant-a --provider dlocal --effect decline` dropped merchant-a/dlocal approval from 0.876 to 0.115. A stored C3 with money after that inject was **not** observed in that run, because 60 seconds is not enough sustained contrast.

A later run with the three compose workers and a longer consume did produce one, through the dashboard toggle: clicked on, `clearwave-worker-merchant-b` logged `incident control: now targeting {'provider': 'adyen'}`, merchant-b/adyen conversion fell from ~90% to 0-18%, `consume --seconds 180 --detect` took 4,254 records with 0 rejected, and detection stored `inc-2026-08-30-715ab9c3` on `{merchant-b, adyen, CO}` at USD 3.89 GMV at risk. Toggled off, conversion recovered and the next sweep returned `incident: null`. **Three minutes of consume, not one**, is what makes the difference.

**Consume a healthy baseline BEFORE you inject, not after.** This is the failure that looks like a
broken detector and is not one. The baseline is a trailing window on the same cohort
(`detector/config.py:BASELINE_TRAILING_BUCKETS`), so if the injection was already running when the
consume started, the whole history detection can see is degraded and there is no contrast in it.
Observed here: merchant-b/adyen sitting at 0.045 conversion against ~0.87 on every other cohort,
6111 records consumed with 0 rejected, and `incident: null` - correctly, because nothing *changed*
inside the window. Healthy consume first, then inject, then keep consuming on the same store.

A run that did produce one, end to end, for the shape to copy: six minutes of healthy traffic into a
fresh store (`--from-latest`), then `worker.inject merchant-b --provider adyen --effect decline
--decline-reason provider_timeout`, then six more minutes on the same consume. Detection stored
`inc-2026-08-30-9797b639` on `{country: CO, merchant-b, adyen}`, conversion 0.653226 -> 0.065217,
GMV at risk USD 4826.64, loss per hour USD 57919.73, severity `high`. `make investigate DB=...` then
diagnosed that stored incident, and severity `high` escalated to dashboard, Slack and phone.

Corrected order, if the broker is already up:

```sh
docker compose up -d kafka schema-registry
# wait until kafka and schema-registry are healthy

# worker first - inject before this is consuming and the command is silently lost
PYTHONUNBUFFERED=1 .venv/bin/python -m worker.worker merchant-a --interval-seconds 0.2

# only after that worker is publishing:
.venv/bin/python -m worker.inject merchant-a --provider dlocal --effect decline

CLEARWAVE_DB=/tmp/clearwave-live.db .venv/bin/python -m detector consume --seconds 60 --detect
```

Then point the dashboard at the same `CLEARWAVE_DB`. Expect `incident: null` after 60 seconds unless you already have minutes of event-time contrast; consume for around three minutes after injecting if you want a stored C3. The judge toggle in the masthead is the other way to inject - on publishes the start command, off publishes the stop - and it targets `merchant-b`/`adyen`, so run the compose workers if you use it.

`--mode anomaly` does not exist (`unrecognized arguments: --mode anomaly`). Replacements that do exist: omit incident flags for healthy traffic and inject after the worker is up; or `--incident-provider dlocal --incident-effect decline`; or `--scenario provider-degradation` on **merchant-c** (not merchant-a). A `--scenario` run stops when `--scenario-duration-seconds` elapses, and both SIGINT and SIGTERM run the shutdown path so the C6 record is closed. Healthy-traffic workers (no `--scenario`) stay unbounded.

`make live` is not one step. Its recipe is `.venv/bin/python -m detector consume --seconds 60 --detect`. It runs on the venv, but it starts neither Kafka nor a worker and does not guarantee an incident. `make e2e` is the one that brings the stack up and consumes; it now consumes for three minutes rather than sixty seconds (`CONSUME_SECONDS`, default 180), because sixty is not enough sustained contrast. It still does not inject for you.

Host worker stdout is block-buffered without `PYTHONUNBUFFERED=1`. The worker image sets this; a host command must too. An empty log is not a dead worker.

Control-topic consumers use `auto.offset.reset=latest` and a new group, so a command published before the worker is up is dropped. Worker first, then inject.

Compose workers run `command: ["merchant-a"]` (etc.) with no `--scenario`, so the long-running healthy-traffic containers never write C6. To produce a scorable record, run a one-off scenario worker that inherits the per-merchant volume (`docker compose run --rm worker-merchant-c merchant-c --scenario provider-degradation --scenario-duration-seconds 12`). The evaluator then reads `state/ground_truth/` from the host: `python3 evaluator/score.py diagnosis.json --store-dir state/ground_truth`.

Standalone `docker-compose` is not required; the `docker compose` subcommand is. Pre-pull images on the demo machine (Schema Registry is a large layer). Prefer `docker compose up -d kafka schema-registry worker-merchant-a worker-merchant-b worker-merchant-c` over a bare `up -d`, which also pulls `devspace`.

More operator detail for the consumer itself: [`docs/live-ingestion.md`](live-ingestion.md).

### Do not run these

| Command | What happens |
| --- | --- |
| `python3 -m worker.worker ... --mode anomaly` | `unrecognized arguments: --mode anomaly` |
| `python3 -m pip install -r detector/requirements.txt` | PEP 668 `externally-managed-environment` |
| `make live` as the demo opening | consume only; no Kafka, no worker, no guaranteed incident |
| Host worker without `PYTHONUNBUFFERED=1` | empty log while the worker is alive |
| Inject, then start the worker | command silently lost |
| `python3 -m investigation.vertical` on system Python | `No module named 'openai'` |
| Judge **Fire hidden incident** with no broker | reports `delivered: false` and injects nothing; start Kafka and the workers first |
| `investigation.vertical --keep` on a prepared store | reseeds anyway; use `--investigate-only` |

## Simulated demo data

All merchants, banks, payments, incidents and outages shown are simulated data produced by this project's simulator for demonstration. Nothing shown represents or implies a real incident, outage, or service problem at any named company. Real company names are used only to make the demonstration recognisable and realistic.

## What the product is

A payment-operations Control Tower for Technical Account Managers. Conversion can silently degrade across providers, issuers, methods, countries, retries, application and infrastructure. The job is not only to notice that conversion dropped. It is to answer where the degradation is, how much money it is costing, what evidence supports the diagnosis, how confident we are, and what the TAM should investigate or do next. The system diagnoses and recommends. It must not automatically remediate production systems. Authoritative wording: [`docs/prd.md`](prd.md) section 1.

## End-to-end flow

Layer names follow [`docs/l4-investigation-prd.md`](l4-investigation-prd.md). Owners follow [`docs/ownership.md`](ownership.md).

1. **L1 Simulated world** - owner W1 (`raul`). Merchant simulators emit native heterogeneous events onto raw Kafka topics. Hidden ground truth stays quarantined. See C1 in `INTERFACES.md`, [`docs/prd.md`](prd.md) sections 5 and 27, and the 2026-08-29T19:04Z Kafka and merchant-shape entries in `DECISIONS.md`.
2. **L2 Ingestion and normalisation** - owner W2 (`andres`). W2 consumes those raw topics, normalises them into one canonical schema (C1b), and persists the canonical representation in SQLite. See C1b in `INTERFACES.md` and the 2026-08-29T19:17Z SQLite entry in `DECISIONS.md`.
3. **L3 Deterministic detection** - owner W2 (`andres`). Detection measures conversion, localises a cohort, prices financial impact, assigns severity from business impact alone, and writes a C3 incident with `lifecycle_state: detected`. No LLM in this layer. See [`docs/contracts/incident.md`](contracts/incident.md) and C3 in `INTERFACES.md`.
4. **L4 Investigation** - owner W3 (`derek`). L4 polls SQLite for detected incidents, claims one, queries C2 evidence tools, and persists a C4 result with cited evidence. It does not consume Kafka. See [`docs/contracts/investigation-result.md`](contracts/investigation-result.md), [`docs/contracts/evidence-tools.md`](contracts/evidence-tools.md), and the 2026-08-29T19:43Z handoff entry in `DECISIONS.md`.
5. **L5 Surfaces and escalation** - owner W4 (`juank`). The dashboard, judge trigger, Slack notification and phone call read C3 and C4 and bind severity to channels. W4 holds no domain logic. See [`docs/prd.md`](prd.md) sections 19, 24 and 25, and the W4 hard rule in [`docs/ownership.md`](ownership.md).

The offline smoke path that must keep printing all five stages is `python3 stubs/slice.py` ([`docs/integration-guide.md`](integration-guide.md)).

## Live demo order

Pitch time is 7 minutes, roughly 3 speaking and 4 with judges operating it ([`DECISIONS.md`](../DECISIONS.md) 2026-08-26T23:43Z). The product experience inside that window is the four-minute sequence in [`docs/prd.md`](prd.md) section 25.

The guaranteed demo path has exactly three scenarios ([`DECISIONS.md`](../DECISIONS.md) 2026-08-29T19:17Z):

1. provider degradation
2. the observationally inseparable provider-versus-issuer confounder
3. a high-impact small-percentage change on a large merchant

Rehearse those three. Remaining scenarios in [`docs/prd.md`](prd.md) section 26 stay documented without a build guarantee. Do not add a fourth guaranteed scenario.

A live run is supposed to fire **one** hidden incident. The judge must not be told which of the three it is ([`docs/prd.md`](prd.md) section 27; W4 owns the trigger control, W1 owns injection). The toggle now does fire one, through `worker.inject`, and no scenario identifier crosses the boundary in either direction. Two caveats before you open with it: it needs the broker and the compose workers up, and it needs roughly three minutes of consume afterwards for detection to have the sustained contrast. The offline Operator runbook above remains the safer opening because it needs no broker at all.

| Step | What appears | Which guaranteed scenarios this step is for |
| --- | --- | --- |
| 0. Establish normal world | Multiple merchants generating realistic traffic. No incident yet. | Baseline for all three. |
| 1. Judge fires a hidden incident | W4 trigger calls W1 injection. Nothing downstream receives a scenario identifier. | Any one of the three, chosen without being revealed. |
| 2. Detector reacts | C3 incident: affected cohort, what changed, onset. Queue is ordered by stored severity, never by recency. | All three. Localisation must work without a hard-coded rule for the slice. |
| 3. Business impact appears | C3 `financial_impact` and `severity` on the incident. Priority is money, not how strong the statistics look. | All three. The high-impact small-percentage large-merchant scenario is the one that specifically proves a small rate change can outrank a dramatic low-volume drop. |
| 4. Investigation starts automatically | L4 claims the detected incident and gathers C2 evidence. | All three. |
| 5. Diagnosis appears | C4: leading hypothesis, competing explanations, diagnostic confidence, recommended action. Severity and confidence stay independent ([`docs/prd.md`](prd.md) section 11). | All three. Provider degradation should be able to name a provider cause. The confounder should keep competing explanations and say why ambiguity exists. The high-impact case still needs a real diagnosis, not just a big number. |
| 6. Critical escalation fires | Channel binding in [`docs/prd.md`](prd.md) section 19: LOW/MEDIUM dashboard; HIGH adds Slack; CRITICAL adds the phone call. Severity is read from C3, never recomputed. | Any of the three whose stored C3 severity is `critical`. This document does not assign a severity to a scenario; W2 does. |
| 7. Judge inspects evidence | Evidence trail: the queries that ran, in order, with what came back. | All three. The confounder is the strongest use of this step: the trail should show why the two causes cannot be separated. |

If L4 is unavailable, the incident still renders with localisation, money and the evidence trail; only the narrative is marked unavailable ([`DECISIONS.md`](../DECISIONS.md) 2026-08-29T19:17Z).

## Already decided and already implemented

Decided, with the authoritative record in parentheses:

- Challenge 02 Control Tower is the pick (`DECISIONS.md` 2026-08-29T16:52Z).
- Four workstreams and owners (`docs/ownership.md`; `DECISIONS.md` 2026-08-29T18:15Z).
- Kafka for raw ingestion; SQLite for persistence; L4 polls SQLite and does not consume Kafka (`DECISIONS.md` 2026-08-29T19:04Z, 19:17Z, 19:43Z).
- C1 through C4 shapes (`INTERFACES.md` and `docs/contracts/`).
- Severity is W2; diagnostic confidence is W3; they never collapse (`docs/ownership.md`; [`docs/prd.md`](prd.md) section 11).
- Three guaranteed demo scenarios, listed above (`DECISIONS.md` 2026-08-29T19:17Z).
- W4 holds no domain logic (`docs/ownership.md`).

Implemented on `origin/main` as of this writing, in the owning trees:

- W1 simulated worker under `worker/`.
- W2 detector, canonical store, live Kafka consumer, and measured C2 tools under `detector/`. `external_status` remains W3's.
- W3 investigation core, evidence gateway, agent loop and evaluator under `investigation/` and `evaluator/`. `metric_series` is on the gateway allowlist.
- W4 dashboard, store, Slack / phone escalation, and a judge-trigger adapter under `surfaces/`. The adapter calls `worker.inject`; the button is a toggle that starts and stops a real incident on a running worker.
- Offline five-stage slice under `stubs/`.

`INTERFACES.md` on `main` still records C5 as named but not specified.

## Still open

From [`docs/ownership.md`](ownership.md) Open decisions, unless a later `DECISIONS.md` entry closed it:

- Team-wide language, framework and stack. Python is decided only for W2's evidence-query scripts (`DECISIONS.md` 2026-08-29T19:17Z) and, separately, for W1's worker (`DECISIONS.md` 2026-08-29T18:39Z).
- Whether the four workstreams are one tree or separate services.
- Concrete numeric thresholds that produce the LOW/MEDIUM/HIGH/CRITICAL labels. The channel binding in [`docs/prd.md`](prd.md) section 19 is decided; the cutovers are not.
- Telephony mechanism, at the ownership-doc level. juank's Twilio path is the accepted W4 implementation, not a new entry in `DECISIONS.md`.
- Which external status sources are actually used.
- How diagnostic confidence is represented, at the ownership-doc level. [`docs/contracts/investigation-result.md`](contracts/investigation-result.md) already publishes qualitative `low` / `medium` / `high`.
- Concrete merchant identities and whether the count is three or four ([`docs/prd.md`](prd.md) section 20).

Also still open on `main`, not for W4 to resolve:

- C5 is not yet the current `INTERFACES.md` shape.
- The judge-trigger seam is closed (PR #45): W4's adapter calls W1's `worker.inject`, and the toggle starts and stops a live incident from the browser.
- juank's request for a high-value transaction id on C2/C3 (`STATUS.md` 2026-08-29T21:07Z) belongs to andres.
- A live `--scenario` run now stops on its declared duration and on SIGINT/SIGTERM, and the evaluator reads closed C6 records from `state/ground_truth/`.

## Where W4 fits

W4 is L5. It owns the dashboard, the judge-facing trigger, Slack, the phone call, the severity-to-channel binding, and demo harness ergonomics ([`docs/ownership.md`](ownership.md)).

Seams W4 **consumes** and does not build:

- **C3 incident record** from W2. Cohort, change, onset, persistence, blast radius, financial impact, severity, lifecycle. Read [`docs/contracts/incident.md`](contracts/incident.md). Do not compute a metric, a severity, or a financial figure. Do not invent field names: use the contract names (`affected_merchants`, `affected_countries`), not whatever the current detector binary happens to emit.
- **C4 investigation result** from W3. Facts, hypothesis, evidence, competing explanations, confidence, recommended action. Read [`docs/contracts/investigation-result.md`](contracts/investigation-result.md). If the outcome is `agent_unavailable`, keep the incident on screen and mark the narrative unavailable.
- **C2** only as already-cited evidence on C4, or as figures that already landed on the C3 record. W4 is not a second measurement path.
- **Judge injection** by calling W1. W4 never reimplements injection and never forwards a scenario identifier toward detection or investigation.

C5 is W4's to specify. The draft on `juank/w4-surfaces` is the accepted approach; it becomes the `INTERFACES.md` shape when that work lands.

W4 produces nothing that L1-L4 read. Escalation is fire-and-forget with a recorded outcome. A channel failing must not fail an incident.
