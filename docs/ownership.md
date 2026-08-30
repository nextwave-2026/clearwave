# Work ownership

The team is four people on independent machines with no shared supervisor, so ownership must be legible from the repository alone. This document makes duplicated work, conflicting changes, and ambiguous ownership structurally unlikely while keeping the workstreams integrable.

## The four workstreams

These are workstreams first and owners second. The assignment below is settled, but a workstream may be worked by more than one person and a person may hold more than one. A contract still has exactly one owning workstream regardless.

| Workstream | Owner | PRD scope | Phase |
|---|---|---|---|
| W1 - Simulated World and Ground Truth | `raul` | PRD sections 5, 7, 8, 9, 20, 26, 27 | 1 |
| W2 - Detection Plane | `andres` | PRD section 3 Product A, 10, 11, 12, 14, 18 | 2 and 3 |
| W3 - Investigation Agent | `derek` | PRD section 3 Product B, 13, 15, 16, 17, 22, 23 | 4 |
| W4 - Surfaces and Escalation | `juank` | PRD sections 19, 24, 25 | 5 and 6 |

### W1 - Simulated World and Ground Truth

**Owns:** merchant profiles and their differentiated traffic shapes; each merchant's native raw event shape and its schema registration; normal non-stationary traffic generation; payment and attempt generation including retry chains; operational/runtime telemetry emission; incident injection; the hidden ground-truth store.

**Produces:** the observable event stream, and nothing else that the rest of the system may read.

**Hard rule:** hidden ground truth must never be reachable from the observable channel. The quarantine in PRD section 5 is this workstream's responsibility to enforce, not the consuming workstreams' responsibility to respect by convention.

### W2 - Detection Plane

**Owns:** event ingestion and normalisation on the consuming side; the canonical ingestion schema; persistence of the normalized representation in the relational SQLite store; retry-aware conversion measurement at both payment level and attempt level; cohort slicing across the dimension set; contextual baseline construction; anomaly detection; anomaly-to-incident qualification; severity and priority assignment; financial impact calculation; the incident store and multi-incident state.

**Hard rule:** no LLM call appears anywhere in this workstream. PRD section 4 makes this a product principle; here it is also the ownership line, which makes violations visible as a dependency rather than as a judgement call.

### W3 - Investigation Agent

**Owns:** the investigation loop; evidence retrieval as a **consumer** of W2's query surface; hypothesis generation and comparison; LLM prompting and response handling; external corroboration adapters (provider health, status pages, public outage signals); the uncertainty and diagnostic-confidence model; missing-evidence identification; the recommendation set; the TAM-facing narrative that answers PRD section 16's six questions; the deterministic scenario catalogue covering PRD section 26; the evaluator that compares a produced diagnosis against hidden truth; integration across the workstreams.

**Hard rule:** it never computes a metric from raw events. If the investigation needs a statistic that W2's query surface does not expose, it requests the addition from W2; it does not compute it locally. This is the single most important anti-duplication rule in the document, because a parallel aggregation path inside the investigator would silently produce a second, divergent answer to "what happened".

### W4 - Surfaces and Escalation

**Owns:** the dashboard and all its views; the judge-facing trigger control; Slack-style notification; phone-call escalation; the escalation channel binding; the end-to-end demo harness and run scripts; startup and health-check ergonomics.

**Hard rule:** it holds no domain logic. Every number it shows is read from W2 or W3 and cited, never recomputed and never derived. A figure that appears only in the UI is a defect.

## Frozen contracts and their single owners

PRD section 32 requires one canonical definition for the event contract, the incident object, simulator ground truth, detector output, and investigation input/output. These are the six named contracts:

| Contract | Owner | Consumers | What it carries |
|---|---|---|---|
| **C1 Raw event shapes** | W1 | W2 | Three JSON-Schema-registered Kafka topics shared by all merchants: `payments.attempts`, `payments.closed`, and `ops.telemetry`. Merchants differ in generated behaviour and traffic mix, not in a forced per-merchant wire schema. |
| **C1b Canonical ingestion schema** | W2 | W2, W3, W4 | One normalized model for downstream payment-attempt analysis, with closed decline vocabulary and event-time semantics. W2 persists normalized records and auxiliary topic data in the relational SQLite store. |
| **C2 Evidence-query tools** | W2 | W3, W4 | Eleven standalone tools over measured behaviour: conversion, decline distribution, retries, cohort comparison, baseline expectation, operational metrics, incident history, external status, financial impact, and metric series. |
| **C3 Incident record** | W2 | W3, W4 | The detector's output: affected cohort, what changed and by how much, onset, persistence, blast radius, financial impact, severity/priority, and lifecycle state. It carries no diagnostic confidence and no root cause; those belong to C4. |
| **C4 Investigation result** | W3 | W4 | Its input is a C3 record. Its output is the PRD section 13 shape: confirmed facts, leading hypothesis, supporting evidence, competing explanations, why the ambiguity exists, missing evidence, diagnostic confidence, and the recommended next action. Each evidence item cites the C2 query that produced it. |
| **C5 Notification and escalation payload** | W4 | none inside the system | What is sent to a channel, and the severity-to-channel binding. |
| **C6 Hidden ground truth and evaluator verdict** | W1; evaluator `derek` | **Evaluator only** | Explicitly quarantined: W2, W3 and W4 must have no read path to it, and the evaluator runs outside the diagnostic path, after the fact. |

## Contested seams, resolved

| Seam | Resolution | Reason |
|---|---|---|
| Financial impact arithmetic | **W2** | PRD section 22 forbids the LLM inventing financial calculations, so the math is deterministic; W3 and W4 cite W2's figure. |
| Severity and priority | **W2** | It is a function of business impact, which W2 measures. |
| Diagnostic confidence | **W3** | It is a function of evidence strength, which only the investigation assesses. PRD section 11 makes severity and confidence independent; splitting their owners is what keeps them from collapsing into one score. |
| Dimension set that cohorts are sliced on | Defined once in **C1 by W1**; W2 slices it and never extends it locally. | A dimension W2 needs is a C1 change requested from W1. |
| Payment-versus-attempt semantics | Generated by **W1** in C1, measured by **W2**. | Neither redefines the other's half. |
| Judge trigger | The control surface is **W4**, the injection endpoint behind it is **W1**. | W4 calls it; W4 never reimplements injection. |
| Scenario definitions and expected outcomes | **W3** | W2 and W3 are graded against them and must not read them, or the scenarios stop measuring generalisation. |
| Retry-amplification detection | Retry chains are emitted by **W1**, amplification is measured by **W2**, and its meaning as evidence is interpreted by **W3**. | Each stage owns its part. |
| External status-source disagreement (PRD section 12 scenario, section 15 rule) | **W3** | It is a weighting judgement over evidence, not a measurement. |

## Rules that keep the boundaries real

1. One workstream per pull request. A pull request touches exactly one workstream's tree, plus the coordination files if needed. A change spanning two workstreams is two pull requests with an `INTERFACES.md` update between them.
2. No cross-tree edits. If your work needs a change in another workstream's tree, you request it - `INTERFACES.md` for the shape, `STATUS.md` to say you need it - and you do not make it yourself, even when it is one line and obviously correct.
3. A contract has exactly one definition, in its owner's tree. Other workstreams import it. Redefining a contract locally, including "just for now", is the failure this whole document exists to prevent.
4. Contract changes are additive during the build window. A breaking change stops work on that boundary: raise it in `INTERFACES.md`, agree, and record it in `DECISIONS.md` before either side moves.
5. Every `INTERFACES.md` entry names its owning workstream. An entry without an owner is not a boundary, it is an ambiguity.
6. Cite, never recompute. Any number displayed or reasoned about carries the contract call that produced it.
7. Stub first, then replace. The vertical slice lands stubs at every seam; each workstream replaces its own stub in place, so nobody is blocked waiting for another workstream to become real.

### Working method inside a workstream

What binds everyone is the seams: the contracts, the ownership boundaries, the coordination files, and the numbered rules in this document. How each contributor works inside their own workstream - tooling, branching cadence, whether and how they use agents, and review habits - is their own choice and is deliberately not specified here. Nothing in this document should be read as requiring a shared internal workflow. It is not structurally defined, on purpose.

## Integration before parallelism

PRD section 32's sequencing gate applies: before the four workstreams run in parallel, one thin end-to-end path must exist - simulator to detector to incident to investigator to surface - with a stub at every seam. The gate for opening parallel work is that C1 through C4 are written down in `INTERFACES.md` and the stubbed path runs end to end. Parallel work that starts before that gate produces the exact incompatible-contract failure PRD section 32 warns about.

W1 has no upstream dependency and is on the critical path for everyone else, which is why it starts first and why its contract is frozen earliest.

### Integration ownership

Integration across all four workstreams is owned by `derek`, in addition to W3. The scenario catalogue and evaluator now sit with integration because they are integration and validation concerns. This covers the end-to-end path, the stubbed vertical slice, keeping the four contracts coherent with each other, and continuous end-to-end validation as components land. It is cross-cutting and transfers no ownership of any workstream internals: each workstream still owns its own tree and its own contracts. A disagreement about a contract is still raised and settled the way this document already specifies - `INTERFACES.md` for the shape, `DECISIONS.md` for the call. Integration ownership adds no new authority over that.

## Current implementation choices and remaining gaps

The demo path now has a concrete runtime: Python code in this repository, Kafka and Schema Registry
for live ingestion, SQLite as the shared evidence store, Docker Compose for the live stack, a
bounded OpenAI Responses API investigation loop, and a plain web dashboard with Slack and Twilio
phone adapters. These choices are recorded in `DECISIONS.md`, `INTERFACES.md`, and the contract
documents under `docs/contracts/`.

Still unresolved or explicitly deferred:

- Replayable long-history data for a real seasonal hour-of-week baseline. `make stack-up` prepares
  eight healthy event-time hours for the demo baseline; it is not a seasonal model.
- Concrete external corroboration sources beyond the current adapter contract.
- The deferred `payment_integrity` evidence surface and any high-value-transaction C2/C3 addition.
- Any non-demo production deployment packaging, scaling work, or remediation capability.

These boundaries are a coordination instrument, not an org chart: a workstream may be worked by more
than one person, and a person may hold more than one, but a contract has exactly one owner
regardless.
