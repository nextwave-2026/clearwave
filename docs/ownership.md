# Work ownership

The team is four people on independent machines with no shared supervisor, so ownership must be legible from the repository alone. This document makes duplicated work, conflicting changes, and ambiguous ownership structurally unlikely while keeping the workstreams integrable.

## The four workstreams

These are **workstreams, not people**. Which person takes which workstream is an open decision recorded at the end of this document, not settled here.

| Workstream | PRD scope | Phase |
|---|---|---|
| W1 - Simulated World and Ground Truth | PRD sections 5, 7, 8, 9, 20, 26, 27 | 1 |
| W2 - Detection Plane | PRD section 3 Product A, 10, 11, 12, 14, 18 | 2 and 3 |
| W3 - Investigation Agent | PRD section 3 Product B, 13, 15, 16, 17, 22, 23 | 4 |
| W4 - Surfaces and Escalation | PRD sections 19, 24, 25 | 5 and 6 |

### W1 - Simulated World and Ground Truth

**Owns:** merchant profiles and their differentiated traffic shapes; normal non-stationary traffic generation; payment and attempt generation including retry chains; operational/runtime telemetry emission; incident injection; the hidden ground-truth store; the deterministic scenario catalogue covering PRD section 26; the evaluator that compares a produced diagnosis against hidden truth.

**Produces:** the observable event stream, and nothing else that the rest of the system may read.

**Hard rule:** hidden ground truth must never be reachable from the observable channel. The quarantine in PRD section 5 is this workstream's responsibility to enforce, not the consuming workstreams' responsibility to respect by convention.

### W2 - Detection Plane

**Owns:** event ingestion and normalisation on the consuming side; operational persistence; retry-aware conversion measurement at both payment level and attempt level; cohort slicing across the dimension set; contextual baseline construction; anomaly detection; anomaly-to-incident qualification; severity and priority assignment; financial impact calculation; the incident store and multi-incident state.

**Hard rule:** no LLM call appears anywhere in this workstream. PRD section 4 makes this a product principle; here it is also the ownership line, which makes violations visible as a dependency rather than as a judgement call.

### W3 - Investigation Agent

**Owns:** the investigation loop; evidence retrieval as a **consumer** of W2's query surface; hypothesis generation and comparison; LLM prompting and response handling; external corroboration adapters (provider health, status pages, public outage signals); the uncertainty and diagnostic-confidence model; missing-evidence identification; the recommendation set; the TAM-facing narrative that answers PRD section 16's six questions.

**Hard rule:** it never computes a metric from raw events. If the investigation needs a statistic that W2's query surface does not expose, it requests the addition from W2; it does not compute it locally. This is the single most important anti-duplication rule in the document, because a parallel aggregation path inside the investigator would silently produce a second, divergent answer to "what happened".

### W4 - Surfaces and Escalation

**Owns:** the dashboard and all its views; the judge-facing trigger control; Slack-style notification; phone-call escalation; the escalation channel binding; the end-to-end demo harness and run scripts; startup and health-check ergonomics.

**Hard rule:** it holds no domain logic. Every number it shows is read from W2 or W3 and cited, never recomputed and never derived. A figure that appears only in the UI is a defect.

## Frozen contracts and their single owners

PRD section 32 requires one canonical definition for the event contract, the incident object, simulator ground truth, detector output, and investigation input/output. These are the six named contracts:

| Contract | Owner | Consumers | What it carries |
|---|---|---|---|
| **C1 Payment attempt event** | W1 | W2 | The PRD section 7 field set: identity (payment id, attempt id, merchant, provider, provider connection), timing, payment dimensions (merchant, provider, method, card network, country, issuing bank, issuer identifier), result (approved/declined/error/pending, normalised decline reason, native response, retry status, attempt number), economics (amount, currency), and operational dimensions (latency, timeout, service error, queue delay, queue depth, retry count, deployment identity, service identity, runtime health). It must preserve the payment-versus-attempt distinction required by PRD section 8. |
| **C2 Cohort and metric query** | W2 | W3, W4 | The single read surface over measured behaviour: conversion at payment and attempt level, decline distribution, retry rates, cohort comparison, baseline expectation, and the operational metrics, sliced by any combination of the C1 dimensions. |
| **C3 Incident record** | W2 | W3, W4 | The detector's output: affected cohort, what changed and by how much, onset, persistence, blast radius, financial impact, severity/priority, and lifecycle state. It carries no diagnostic confidence and no root cause; those belong to C4. |
| **C4 Investigation result** | W3 | W4 | Its input is a C3 record. Its output is the PRD section 13 shape: confirmed facts, leading hypothesis, supporting evidence, competing explanations, why the ambiguity exists, missing evidence, diagnostic confidence, and the recommended next action. Each evidence item cites the C2 query that produced it. |
| **C5 Notification and escalation payload** | W4 | none inside the system | What is sent to a channel, and the severity-to-channel binding. |
| **C6 Hidden ground truth and evaluator verdict** | W1 | **W1 only** | Explicitly quarantined: W2, W3 and W4 must have no read path to it, and the evaluator runs outside the diagnostic path, after the fact. |

## Contested seams, resolved

| Seam | Resolution | Reason |
|---|---|---|
| Financial impact arithmetic | **W2** | PRD section 22 forbids the LLM inventing financial calculations, so the math is deterministic; W3 and W4 cite W2's figure. |
| Severity and priority | **W2** | It is a function of business impact, which W2 measures. |
| Diagnostic confidence | **W3** | It is a function of evidence strength, which only the investigation assesses. PRD section 11 makes severity and confidence independent; splitting their owners is what keeps them from collapsing into one score. |
| Dimension set that cohorts are sliced on | Defined once in **C1 by W1**; W2 slices it and never extends it locally. | A dimension W2 needs is a C1 change requested from W1. |
| Payment-versus-attempt semantics | Generated by **W1** in C1, measured by **W2**. | Neither redefines the other's half. |
| Judge trigger | The control surface is **W4**, the injection endpoint behind it is **W1**. | W4 calls it; W4 never reimplements injection. |
| Scenario definitions and expected outcomes | **W1** | W2 and W3 are graded against them and must not read them, or the scenarios stop measuring generalisation. |
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

## Integration before parallelism

PRD section 32's sequencing gate applies: before the four workstreams run in parallel, one thin end-to-end path must exist - simulator to detector to incident to investigator to surface - with a stub at every seam. The gate for opening parallel work is that C1 through C4 are written down in `INTERFACES.md` and the stubbed path runs end to end. Parallel work that starts before that gate produces the exact incompatible-contract failure PRD section 32 warns about.

W1 has no upstream dependency and is on the critical path for everyone else, which is why it starts first and why its contract is frozen earliest.

## Open decisions

Each item below is explicitly unresolved. Answers are recorded in `DECISIONS.md`.

- **Which person owns which workstream.** The team's stated backgrounds are in the `README.md` team section and are input to that call, not the call itself.
- **Language, framework and stack.** Still open by the existing `DECISIONS.md` entry; PRD section 21's preferences are not a decision. Blocks the concrete directory names in this document, which are deliberately left abstract until then.
- **Transport between simulator and detector:** a Kafka-like stream or something simpler. PRD section 21 explicitly permits simplifying, and PRD section 29 prefers simple process boundaries.
- **Persistence choice for operational and historical data.**
- **Whether the four workstreams live in one repository tree or separate services.** Follows the stack decision.
- **The concrete severity thresholds that bind to dashboard, Slack and phone channels.** PRD section 19 gives the shape and says thresholds are tuned later.
- **The telephony mechanism for the phone-call escalation.** PRD section 19 requires a free or effectively free route.
- **Which external status or corroboration sources are actually used, if any.**
- **How diagnostic confidence is represented:** qualitative levels or a calibrated number. PRD section 13 prefers qualitative and rules out fabricated percentages, but does not fix the scale.
- **The concrete merchant identities and whether the count is three or four, within the PRD section 20 shape.**

These boundaries are a coordination instrument, not an org chart: a workstream may be worked by more than one person, and a person may hold more than one, but a contract has exactly one owner regardless.
