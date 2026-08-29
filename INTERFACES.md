# Interfaces

Edit this file in place to record the current state of each interface boundary. Do not append historical versions. This file is intentionally not configured with the union merge driver: union would keep both the old and new value of an interface, leaving contradictory claims in one file. A merge conflict here is a genuine signal that any two contributors are editing the same boundary and should stop to reconcile it.

**Commit this file straight to `main`** - no branch, no pull request. Unlike the append-only logs this
file is NOT union-merged, so it can genuinely conflict. A conflict here means any two contributors are changing the same boundary at once: agree on the shape, then commit the agreed version.

- **Boundary name:** C1 Raw per-merchant event shapes
- **Owner:** W1 - Simulated World and Ground Truth (`raul`)
- **Current shape:** Each merchant simulator emits its own native event shape; the deliberate heterogeneity mirrors the real orchestrator problem. W1 registers each raw shape in the schema registry. W2 consumes the registered raw topics for normalisation.
- **Last changed:** 2026-08-29T19:04Z

- **Boundary name:** C1b Canonical ingestion schema
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** One normalized model for every downstream component, expressed as JSON Schema in the schema registry. The canonical topic carries the normalized stream; W2 persists it in a relational SQLite store. W2, W3 and W4 consume this consistent model.
- **Last changed:** 2026-08-29T19:17Z

- **Boundary name:** C2 Evidence-query tools
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** Specified in full in `docs/contracts/evidence-tools.md`, which is the single definition. Ten tools, each a standalone Python 3 subprocess reading one JSON object on stdin and writing one on stdout: `cohort_metrics`, `cohort_compare`, `drilldown`, `decline_breakdown`, `retry_stats`, `operational_metrics`, `confounding_check`, `incident_history`, `external_status`, `financial_impact`. Every successful response carries `query_id` and `as_of`. Payment-level and attempt-level conversion stay explicit and are never collapsed. Fixture-backed reference stubs are in `stubs/evidence/`; W2 replaces each with real measurement. Callers cite `query_id` and never compute a metric themselves. Open: no tool returns a metric over a series of time buckets, which C3's `onset` and severity's trajectory both need.
- **Last changed:** 2026-08-29T19:38Z

- **Boundary name:** C3 Incident record
- **Owner:** W2 - Detection Plane (`andres`)
- **Current shape:** Specified in full in `docs/contracts/incident.md`, which is the single definition. Carries `affected_cohort`, `change`, `onset`, `persistence`, `blast_radius`, `financial_impact`, `severity` and `lifecycle_state`, and deliberately has no `root_cause`, `hypothesis` or `diagnostic_confidence` field. Severity is a business-impact-only function of log-scaled loss per hour, blast radius, persistence and trajectory, and never of statistical strength. Open: nothing yet defines what transports a C3 record to the investigation agent or what starts an investigation.
- **Last changed:** 2026-08-29T19:38Z

- **Boundary name:** C4 Investigation result
- **Owner:** W3 - Investigation Agent
- **Current shape:** Contract is named and owned but not yet specified. Specifying it is the gate on opening parallel work.
- **Last changed:** 2026-08-29T17:00Z

- **Boundary name:** C5 Notification and escalation payload
- **Owner:** W4 - Surfaces and Escalation
- **Current shape:** Contract is named and owned but not yet specified. Specifying it is the gate on opening parallel work.
- **Last changed:** 2026-08-29T17:00Z

- **Boundary name:** C6 Hidden ground truth and evaluator verdict
- **Owner:** W1 - Simulated World and Ground Truth (`raul`); evaluator: `derek` (integration)
- **Current shape:** Hidden ground truth remains quarantined in W1. The evaluator compares diagnosis against it after the fact; detection and investigation have no read path to the ground truth.
- **Last changed:** 2026-08-29T19:04Z
