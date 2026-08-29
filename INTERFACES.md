# Interfaces

Edit this file in place to record the current state of each interface boundary. Do not append historical versions. This file is intentionally not configured with the union merge driver: union would keep both the old and new value of an interface, leaving contradictory claims in one file. A merge conflict here is a genuine signal that any two contributors are editing the same boundary and should stop to reconcile it.

**Commit this file straight to `main`** - no branch, no pull request. Unlike the append-only logs this
file is NOT union-merged, so it can genuinely conflict. A conflict here means any two contributors are changing the same boundary at once: agree on the shape, then commit the agreed version.

- **Boundary name:** C1 Raw per-merchant event shapes
- **Owner:** W1 - Simulated World and Ground Truth (`raul`)
- **Current shape:** Three JSON-Schema-registered topics, one schema per topic shared across all three merchants, as built and verified by W1 and settled by `docs/adr/0014-w1-raw-events-share-one-schema-per-topic.md`, which andres confirmed at 2026-08-29T21:30Z. The registered schemas in `worker/registry/*.schema.json` are the authoritative field list.
  - `payments.attempts` (`clearwave.attempt.v1`), keyed by `payment_id` so a payment's whole retry chain stays ordered on one partition. One record per provider attempt.
  - `payments.closed` (`clearwave.payment_closed.v1`), keyed by `payment_id`. One record per payment once its chain reaches a terminal state, never per attempt.
  - `ops.telemetry` (`clearwave.ops.v1`), keyed by `service_id`. A periodic per-service gauge sample with no payment identity.
  - `event_id` is globally unique on every topic and W2 dedupes on it, which is what turns at-least-once delivery into exactly-once counting. `decline_reason` comes from W1's frozen enum; W2 maps it into C1b's closed vocabulary and never asks W1 to widen it.
  - The earlier plan of one raw topic per merchant (`raw.<merchant_id>`) and the Avro single shared schema before it are both superseded, not carried forward. Nothing subscribes to `raw.*`.
  - `city`/`lat`/`lon` (geography, reference-table-driven per country) is carried on the attempt shape as an additional field. It does not survive into C1b: the cohort dimension set in `docs/contracts/canonical-event.md` is unchanged, and W2 drops the field at normalisation rather than slicing on it.
  - Still open, unchanged: replayable backfill history. The contextual hour-of-week baseline waits on it.
- **Last changed:** 2026-08-29T21:30Z

- **Boundary name:** C1b Canonical ingestion schema
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** Specified in full in `docs/contracts/canonical-event.md`, which is the single definition, and enforced by `detector/schema.py`. One normalized model for every downstream component; W2 persists it in a relational SQLite store located by `CLEARWAVE_DB`. W2, W3 and W4 consume this consistent model. W2 now consumes all three C1 topics live from Kafka (`detector/consumer.py`, run with `python3 -m detector consume`; see `docs/live-ingestion.md`) into that same store, through the same normalisation the file-based `ingest` path uses - there is one normalisation path, not two. Ingestion is idempotent on `event_id` across all three record kinds, offsets advance only after the store write is durable, and everything is bucketed on event time so a replay reproduces a live run. An unrecognised shape, currency or decline reason is dead-lettered with its reason and its source, never silently dropped. `payments.attempts` becomes the canonical event; `ops.telemetry` and `payments.closed` are normalised and persisted as their own record kinds and are not canonical events. The file-based path (`seed`, `ingest`, `detect`) stays independent of Kafka and remains the broker-free demo fallback.
- **Last changed:** 2026-08-29T21:30Z

- **Boundary name:** C2 Evidence-query tools
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** Specified in full in `docs/contracts/evidence-tools.md`, which is the single definition. C2 is an interface contract, not an implementation roster: it has eleven standalone Python 3 tools - `cohort_metrics`, `cohort_compare`, `drilldown`, `decline_breakdown`, `retry_stats`, `operational_metrics`, `confounding_check`, `incident_history`, `external_status`, `financial_impact`, and `metric_series` - each reading one JSON object on stdin and writing one on stdout. The other ten tools are implemented by W2; `external_status` is implemented by W3 because implementation ownership follows the data source. Every successful response carries `query_id` and `as_of`. Payment-level and attempt-level conversion stay explicit and are never collapsed. Fixture-backed reference stubs are in `stubs/evidence/`; W2 replaces its ten tools with real measurement and W3 replaces the `external_status` fixture behind the unchanged contract. Callers cite `query_id` and never compute a metric themselves. `metric_series` returns an ordered event-time-bucketed series for onset, severity trajectory and L4's narrative. W2's ten tools are now wired to real measurement over one SQLite store located by the `CLEARWAVE_DB` environment variable, defaulting to `state/clearwave.db`; consumers must read the same file. An empty store is a well-formed answer - zero counters, nulls for undefined rates, empty lists - not an error, and the error envelope is reserved for malformed input. `as_of` is the measurement watermark rather than wall-clock now, so a response is reproducible on replay. `operational_metrics.runtime_health` is now measured from consumed `ops.telemetry` samples rather than always reporting `unobserved`: with no sample it answers `unobserved` with its reason exactly as before, so nothing built against that shape breaks, and with samples it reports the gauges W1 publishes plus the criterion behind the verdict. `service_health` is unchanged and stays derived from first-party attempts; the two are deliberately separate answers.
- **Last changed:** 2026-08-29T21:30Z

- **Boundary name:** C3 Incident record
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** Specified in full in `docs/contracts/incident.md`, which is the single definition. Carries `affected_cohort`, `change`, `onset`, `persistence`, `blast_radius`, `financial_impact`, `severity` and `lifecycle_state`, and deliberately has no `root_cause`, `hypothesis` or `diagnostic_confidence` field. Severity is a business-impact-only function of log-scaled loss per hour, blast radius, persistence and trajectory, and never of statistical strength. Handoff is settled by `docs/adr/0004-l4-consumes-no-kafka.md` and the Persistence and query behaviour section of `docs/l4-investigation-prd.md`: L4 reads detected incidents from the relational SQLite store, claims one, and persists the result before moving it to `diagnosed`; `lifecycle_state: detected` is the sole handoff signal. W2 must durably write C3 with that state; W2 does not call L4.
- **Last changed:** 2026-08-29T19:43Z

- **Boundary name:** C4 Investigation result
- **Owner:** W3 - Investigation Agent (`derek`)
- **Current shape:** Specified in full in `docs/contracts/investigation-result.md`, which is the single definition. Carries confirmed facts, a leading hypothesis, supporting evidence, competing explanations, why ambiguity exists, missing evidence, diagnostic confidence and recommended action. Every evidence item cites a `query_id`. It deliberately has no severity field. The four outcomes are `diagnosed`, `ambiguous`, `insufficient_evidence` and `agent_unavailable`.
- **Last changed:** 2026-08-29T19:43Z

- **Boundary name:** C5 Notification and escalation payload
- **Owner:** W4 - Surfaces and Escalation (`juank`)
- **Current shape:** Specified in full in `docs/contracts/notification-escalation.md`, which is the single definition. Built by W4 from one C3 record and, when available, the latest C4 result; it has no consumers inside the system and invents no figure. Carries the incident identity and lifecycle state, affected cohort, the measured change, financial impact, severity from C3, and diagnostic confidence, leading hypothesis and competing explanations from C4 - severity and diagnostic confidence stay separate fields and are never blended. The severity-to-channel binding is `low`/`medium` to dashboard, `high` adds Slack, `critical` adds the phone call, implemented in `surfaces/escalation.py`. Every channel is fire-and-forget with a recorded outcome: a failing channel never blocks the dashboard and never fails an incident.
- **Last changed:** 2026-08-29T23:23Z

- **Boundary name:** C6 Hidden ground truth and evaluator verdict
- **Owner:** W1 - Simulated World and Ground Truth (`raul`); evaluator: `derek` (integration)
- **Current shape:** Specified in full in `docs/contracts/hidden-truth.md`, which is the single definition. Implementation: `worker/ground_truth/store.py` is the quarantined SQLite store (local to W1's own process, never mounted or exposed to W2/W3); `worker/ground_truth/scenarios.py` maps the three guaranteed scenarios in `docs/scenarios.md` onto real merchant/provider/bank data; `worker/ground_truth/runner.py` records the injected configuration on start and the observed magnitude on close. Detection and investigation have no read path to it - only the evaluator, only after a diagnosis exists.
- **Last changed:** 2026-08-29T22:15Z
