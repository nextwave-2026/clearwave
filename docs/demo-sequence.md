# Demo sequence

Orientation for a live Control Tower demo. This file is not a second product baseline.

- Product direction: [`docs/prd.md`](prd.md)
- Workstreams and seams: [`docs/ownership.md`](ownership.md)
- Settled calls: [`DECISIONS.md`](../DECISIONS.md)
- Current contract shapes: [`INTERFACES.md`](../INTERFACES.md)
- Frozen contracts: [`docs/contracts/`](contracts/)

If this file and the PRD disagree, the PRD governs. Correct the disagreement in `DECISIONS.md`, not here.

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

A live run fires **one** hidden incident. The judge must not be told which of the three it is ([`docs/prd.md`](prd.md) section 27; W4 owns the trigger control, W1 owns injection). The same surface sequence runs for whichever of the three is fired.

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

- W1 simulated worker under `worker/` (merged via #28).
- W2 detector, canonical store and measured C2 tools under `detector/` (including #26). `external_status` remains W3's.
- W3 investigation core, evidence gateway, agent loop and evaluator under `investigation/` and `evaluator/`.
- Offline five-stage slice under `stubs/`.

W4 is not on `main` yet. The accepted W4 path is juank's `juank/w4-surfaces` work: dashboard, store, judge-trigger adapter, Slack Block Kit, Twilio Programmable Voice over `urllib` with dashboard-call fallback, and a C5 draft at `docs/contracts/notification-escalation.md` on that branch. Until that merges, `INTERFACES.md` on `main` still records C5 as named but not specified.

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
- `metric_series` is a published C2 tool but is not yet on W3's gateway allowlist (`STATUS.md` 2026-08-29T20:35Z / 20:53Z).
- Detector `blast_radius` field names currently disagree with [`docs/contracts/incident.md`](contracts/incident.md). That mismatch is W2's to fix. See the STATUS line that landed with this file.
- juank's request for a high-value transaction id on C2/C3 (`STATUS.md` 2026-08-29T21:07Z) belongs to andres.
- The certifi MPL-2.0 licence question (`STATUS.md` 2026-08-29T20:49Z).

## Where W4 fits

W4 is L5. It owns the dashboard, the judge-facing trigger, Slack, the phone call, the severity-to-channel binding, and demo harness ergonomics ([`docs/ownership.md`](ownership.md)).

Seams W4 **consumes** and does not build:

- **C3 incident record** from W2. Cohort, change, onset, persistence, blast radius, financial impact, severity, lifecycle. Read [`docs/contracts/incident.md`](contracts/incident.md). Do not compute a metric, a severity, or a financial figure. Do not invent field names: use the contract names (`affected_merchants`, `affected_countries`), not whatever the current detector binary happens to emit.
- **C4 investigation result** from W3. Facts, hypothesis, evidence, competing explanations, confidence, recommended action. Read [`docs/contracts/investigation-result.md`](contracts/investigation-result.md). If the outcome is `agent_unavailable`, keep the incident on screen and mark the narrative unavailable.
- **C2** only as already-cited evidence on C4, or as figures that already landed on the C3 record. W4 is not a second measurement path.
- **Judge injection** by calling W1. W4 never reimplements injection and never forwards a scenario identifier toward detection or investigation.

C5 is W4's to specify. The draft on `juank/w4-surfaces` is the accepted approach; it becomes the `INTERFACES.md` shape when that work lands.

W4 produces nothing that L1-L4 read. Escalation is fire-and-forget with a recorded outcome. A channel failing must not fail an incident.
