# Accepted product baseline

This is the authoritative product baseline for Challenge 02, accepted 2026-08-29.

Sections 1 to 31 are the product baseline.
Sections 32 to 33 describe how the build is orchestrated.
The concrete four-way division of that work lives in `docs/ownership.md`.

Where this document and any other document in the repository disagree about product direction,
this document governs. Correct the disagreement by appending to `DECISIONS.md`.

The challenge brief itself remains `docs/challenge.md`.
This PRD is our accepted response to that brief.

The technology preferences in section 21 are preferences, not decisions.
No stack decision has been made.

# NextWave Hackathon 2026 — Challenge 02 “Control Tower”
## Build Handoff for Orchestration Agent

You are taking over implementation of our NextWave Hackathon 2026 project for Challenge 02, **Control Tower**.

This is now an execution session, not a brainstorming session. The product direction below is the accepted baseline. You may challenge an implementation choice if it threatens correctness, demo reliability, or the 24-hour schedule, but do not casually redesign the product.

Your job is to orchestrate the build, parallelize work where useful, keep the system coherent, and continuously validate against the judging scenario.

---

# 1. Mission

We are building a payment-operations Control Tower for Technical Account Managers.

A payment orchestration platform sits between merchants and payment providers. Conversion can silently degrade because of:

- payment-provider degradation,
- issuing-bank over-declines,
- payment-method failures,
- country-specific failures,
- routing problems,
- retry amplification,
- application failures,
- infrastructure failures,
- deployment/configuration problems,
- queue buildup,
- latency,
- code/runtime failures,
- or combinations of several dimensions.

The current operational problem is not merely detecting that conversion dropped.

The difficult part is determining:

> **Where is the degradation occurring, how much money is it costing, what evidence supports the diagnosis, how confident are we, and what should the TAM investigate or do next?**

The system diagnoses and recommends.

It **must not automatically remediate production systems**.

---

# 2. Judging priorities

Optimize in this order:

1. **It must actually work live.**
2. **Every important decision must be defensible.**
3. **It must handle the ugly cases in the stated problem.**
4. Originality.
5. Experience / presentation quality.

Do not sacrifice reliability for additional features.

A simpler system that produces a correct, explainable diagnosis is preferable to a sophisticated system whose behavior we cannot defend.

---

# 3. Fundamental product architecture

Treat this conceptually as **two cooperating products in one repository/system**.

## Product A — Control Tower / Detection Plane

Continuously monitors observable telemetry.

Responsibilities:

- consume payment and operational events,
- establish contextual normal behavior,
- detect meaningful deviations,
- suppress normal noise,
- identify affected cohorts,
- calculate business impact,
- assign incident severity,
- create investigation candidates,
- maintain incident state.

This layer must be primarily **deterministic and evidence-driven**.

The LLM must not be responsible for deciding whether raw traffic “looks anomalous.”

---

## Product B — Investigation / Research Agent

Receives an already-detected anomaly or incident.

Responsibilities:

- investigate the affected cohort,
- compare alternative explanations,
- inspect surrounding cohorts,
- examine decline/error patterns,
- inspect retries and queue behavior,
- correlate service/runtime evidence,
- consult provider/public health information where useful,
- identify supporting and contradicting evidence,
- explicitly identify missing evidence,
- produce a bounded root-cause inference,
- explain the incident for a TAM,
- recommend the next action.

Think of this as:

> **research + investigation + explanation + advice**

rather than:

> “LLM reads logs and decides what happened.”

---

# 4. Core philosophy

The key separation is:

> **Deterministic systems establish what happened.
> The agent investigates why it probably happened.**

Never blur these responsibilities unless there is a compelling reason.

---

# 5. Data environment

No dataset, baseline, transaction corpus, or historical data is provided by the organizers.

We own the complete environment:

- merchant simulation,
- normal traffic,
- historical baseline,
- transaction stream,
- provider-attempt behavior,
- retries,
- queues,
- runtime/service telemetry,
- incident injection,
- test scenarios,
- demo scenarios,
- hidden ground truth.

This gives us control over the demonstration but introduces a major credibility requirement:

## Simulator and diagnostic system must remain independent.

The simulator may know:

- injected cause,
- affected dimensions,
- degradation strength,
- incident start,
- incident end.

The Control Tower must **never receive that hidden state**.

It sees only observable events.

A separate evaluator may compare:

**hidden injected truth**

against

**Control Tower diagnosis**

afterward.

Do not leak simulator ground truth into detection or investigation.

---

# 6. Judge interaction model

The judges will not provide us with a historical dataset.

Our own simulated environment supplies the data.

The judging experience should allow a judge to trigger/fire an incident and then observe the system independently:

1. normal traffic exists,
2. judge triggers an incident,
3. simulator changes observable system behavior,
4. Control Tower detects it,
5. incident is prioritized,
6. investigation starts automatically,
7. evidence accumulates,
8. system produces a diagnosis/inference,
9. TAM-facing explanation appears,
10. notifications/escalations occur.

Nobody on the team should need to manipulate the diagnosis during this sequence.

The diagnostic path must not know the chosen hidden incident configuration.

---

# 7. Transaction/event contract

Use a sufficiently rich normalized payment-attempt model.

At minimum support concepts equivalent to:

### Identity

- payment ID
- transaction / attempt ID
- merchant ID
- provider
- provider connection/account where relevant

### Timing

- created timestamp
- attempt timestamp
- completion timestamp if useful

### Payment dimensions

- merchant
- provider
- payment method
- card/network where applicable
- country
- issuing bank
- BIN/IIN or equivalent issuer identifier where useful

### Result

- approved / declined / error / pending
- normalized decline reason
- provider/native response if available
- retry status
- attempt number

### Economics

- amount
- currency

### Operational dimensions

Where appropriate:

- response latency
- timeout
- HTTP/service error
- queue delay
- queue depth
- retry count
- deployment/application identity
- service/container identity
- runtime health

The exact implementation contract may evolve, but preserve enough information to distinguish customer payments from individual provider attempts.

---

# 8. Critical retry requirement

A single customer payment can create multiple attempts.

Example:

```text
Payment P123
  attempt 1 → provider A → timeout
  attempt 2 → provider A → timeout
  attempt 3 → provider B → declined
```

Do not naïvely calculate customer conversion by treating all attempts as independent payments.

The system must understand the distinction between:

- payment-level conversion,
- attempt-level behavior,
- retry amplification.

Retry storms are themselves operational evidence.

A delayed transaction may accumulate in a queue, retry, fail again, and repeat.

We want the system to be capable of noticing patterns such as:

> underlying provider failure + growing queue + repeated retries + amplified request load.

---

# 9. Baseline behavior

Normal traffic is **not stationary**.

The simulator should produce realistic variation:

- daily/time-of-day patterns,
- merchant-specific volume patterns,
- traffic spikes,
- payment-mix changes,
- noisy decline rates,
- geography changes,
- expected Friday/weekend behavior,
- low-volume cohorts,
- normal transient errors.

Important distinction:

> Historical behavior is not automatically healthy behavior.

For example:

A Friday traffic spike may be expected.

A conversion degradation that repeatedly occurs during Friday traffic peaks may still be a recurring business problem.

Do not simply learn:

> “it happened before, therefore ignore it.”

Baseline behavior should represent contextual expectations while still allowing recurring degradation to be detected as meaningful.

---

# 10. Anomaly versus incident

Preserve this conceptual distinction.

## Anomaly

Behavior that is statistically or operationally unusual.

## Incident

Behavior worth investigating because of one or more of:

- business impact,
- conversion loss,
- financial exposure,
- persistence,
- blast radius,
- operational risk,
- retry amplification,
- corroborating evidence.

Do not assume the statistically strangest event is automatically the most important incident.

Example:

A tiny cohort:

```text
93% → 40%
8 transactions
$120 impact
```

may be less urgent than:

```text
92.0% → 89.5%
very high volume
$25,000/hour estimated loss
```

---

# 11. Severity and confidence are independent

This is a firm design decision.

Do not collapse them into one score.

## Severity / priority

Primarily represents:

- business impact,
- lost approval volume,
- monetary exposure,
- affected traffic,
- blast radius,
- persistence,
- escalation risk.

## Diagnostic confidence

Represents:

- strength of causal evidence,
- statistical support,
- discriminatory evidence,
- amount of supporting information,
- amount of contradictory information,
- remaining ambiguity.

Therefore this is valid:

```text
PRIORITY: CRITICAL
DIAGNOSTIC CONFIDENCE: LOW
```

Example:

> Conversion has collapsed and approximately $18K/hour is at risk, but existing evidence cannot yet distinguish provider degradation from issuer degradation.

Low confidence must **not downgrade an economically catastrophic incident**.

---

# 12. Root-cause granularity

The challenge requires handling incident combinations never explicitly programmed.

The system must therefore avoid a hard-coded catalogue of incident types.

It should be capable of exploring intersections such as:

```text
merchant
× provider
× payment method
× country
× issuer
× decline/error type
```

and operational dimensions when relevant.

Example hidden incident:

```text
Merchant A
→ Mastercard
→ Colombia
→ Bank X
→ Provider P2
```

Broader cohorts may also shift as a side effect.

The system should report the **most specific explanation supported by sufficient evidence**, not blindly choose the finest possible slice.

A nested explanation is acceptable and often preferable.

---

# 13. Honest uncertainty

This is one of the challenge's most important behaviors.

Never fabricate certainty.

Example:

Suppose:

- every Bank X transaction went through Provider P2,
- every failing Provider P2 transaction came from Bank X.

The evidence may not allow us to distinguish:

```text
Provider P2 problem
```

from:

```text
Bank X problem
```

The correct product behavior is something like:

### Confirmed

Conversion degradation is isolated to this cohort.

### Leading hypothesis

Provider P2 degradation.

### Supporting evidence

Explain the observed correlations.

### Competing explanation

Bank X cannot be ruled out.

### Why ambiguity exists

Bank X and P2 are fully confounded in the current observation window.

### Missing evidence

Need:

- P2 traffic from other issuers, or
- Bank X traffic through another provider.

### Recommended next investigation

Gather that comparison before broad rerouting.

Do not invent fake confidence percentages unless they are produced by a genuinely calibrated model.

Qualitative confidence levels are preferable to theatrical precision.

---

# 14. Multi-incident support

The system must tolerate concurrent incidents.

Examples:

- Provider A degraded globally,
- Bank X independently over-declining,
- merchant B simultaneously has a deployment issue.

One broad anomaly must not necessarily absorb every other incident.

Design incident state so independent failures can coexist.

---

# 15. Evidence universe

The investigator should be able to use multiple evidence classes.

## Payment evidence

- approval conversion
- attempt conversion
- decline distribution
- issuer
- provider
- method
- country
- card network
- retry frequency
- affected cohort comparisons

## Operational evidence

- latency
- errors
- timeouts
- retries
- queue depth
- queue delay
- service health
- runtime/container health
- deployment changes
- application logs

## External corroboration

Where available:

- provider health APIs,
- provider status pages,
- public operational status,
- third-party outage signals such as Downdetector-like sources.

Important rule:

> External evidence can strengthen or weaken a hypothesis, but must not replace first-party observational evidence.

If:

```text
payment evidence → Provider A looks degraded
status page → all systems operational
```

the status page must not invalidate our own evidence.

External integration failures must also not break the core demo.

---

# 16. Incident representation

Every surfaced incident should eventually answer these six questions:

## 1. What changed?

Example:

> Approval fell from 92.1% to 63.8%.

## 2. Where?

Example:

> Merchant A → Mastercard → Colombia → Bank X → Provider P2.

## 3. How much does it matter?

Example:

> Estimated $14,800 GMV/hour at risk.

## 4. What probably caused it?

Example:

> Provider-side degradation is the leading hypothesis.

## 5. Why do we believe that?

Show concrete supporting and contradicting evidence.

## 6. What should the TAM do?

Example:

> Investigate Provider P2 and consider rerouting eligible traffic to P3. Do not disable Mastercard globally.

---

# 17. TAM is the primary user

Design explanations for a **Technical Account Manager responsible for merchants**.

The TAM should not need to manually cross-filter dozens of dimensions at 3 a.m.

They should receive:

- what happened,
- affected merchant,
- severity,
- financial consequence,
- likely root cause,
- confidence,
- evidence,
- missing evidence,
- recommended investigation/action.

Avoid explanations that require knowledge of the internal detection implementation.

---

# 18. Financial impact is first-class

Business impact must be visible throughout the system.

At minimum consider metrics such as:

- attempted payment value,
- expected approval rate,
- actual approval rate,
- estimated lost approved volume,
- estimated GMV at risk,
- estimated loss per minute/hour,
- incident cumulative impact.

Keep assumptions clearly labelled.

Do not imply platform revenue loss if the available data only supports GMV-at-risk estimation.

Financial impact should influence priority.

---

# 19. Notifications

Demo scope:

## Keep

- dashboard / product UI
- Slack-style operational notification if practical
- **phone call escalation for critical incidents; high incidents add Slack** ([notification escalation binding](contracts/notification-escalation.md))

## Remove from core scope

- email
- ChatGPT-account integration

The phone call is a core demo requirement.

Find a free or effectively free way to demonstrate it.

However:

Do not let telephony integration jeopardize the detection/diagnosis core.

The call should happen only for sufficiently severe incidents and should reinforce the priority model.

Example escalation:

```text
LOW/MEDIUM → dashboard
HIGH → dashboard + Slack
CRITICAL → dashboard + Slack + phone call
```

Exact thresholds can be tuned later.

---

# 20. Merchant simulation scope

Do not overbuild merchant count.

Prefer a small number of richly differentiated merchants over dozens of meaningless IDs.

Reasonable default:

**3–4 representative merchants.**

Prefer realistic merchants/Yuno-client-like businesses where public technical/status surfaces make the demo more credible.

Give merchants genuinely different profiles, for example:

### Merchant A

High-volume e-commerce.

### Merchant B

Subscription / recurring-oriented business.

### Merchant C

LATAM marketplace with diverse payment methods/geographies.

### Merchant D

Lower-volume but high-ticket travel/commerce.

The point is to create differences in:

- traffic distribution,
- providers,
- countries,
- payment methods,
- financial impact,
- baseline patterns.

Expand beyond this only if it is essentially free.

---

# 21. Candidate technical architecture

The original whiteboard direction was approximately:

```text
merchant/server simulators
        ↓
event ingestion
        ↓
Kafka-like stream / queues
        ↓
normalized contract / registry
        ↓
consumer
        ↓
operational persistence
        ↓
deterministic detection
        ↓
incident store
        ↓
investigation agent
        ↓
evidence + recommendation
        ↓
UI / Slack / call
```

There was also a preference for:

- containerized fake merchant/server instances,
- Kafka/event queues,
- a registry/normalization boundary,
- NoSQL-like historical persistence.

These technologies are **implementation preferences, not product dogma**.

If a simpler technology materially improves the probability that the complete system works within 24 hours, simplify.

Do not build infrastructure merely because it looks enterprise-grade.

---

# 22. LLM role

We have OpenAI model credits available for the domain-expert investigator.

Use the LLM where reasoning adds value.

Good uses:

- hypothesis generation,
- deciding which evidence to inspect next,
- interpreting decline/error relationships,
- correlating multiple evidence sources,
- identifying missing comparisons,
- explaining uncertainty,
- converting technical evidence into TAM language,
- recommending investigation/remediation options.

Bad uses:

- determining baseline conversion from raw logs,
- replacing deterministic aggregation,
- deciding whether every individual event is anomalous,
- inventing financial calculations,
- fabricating unavailable evidence.

Every major LLM claim should be traceable to evidence supplied by deterministic systems or external evidence retrieval.

---

# 23. Recommendations, not remediation

Possible recommendations include:

- investigate provider health,
- inspect specific issuer behavior,
- reroute eligible traffic to fallback provider,
- inspect deployment,
- inspect queue,
- investigate retry behavior,
- escalate to provider,
- contact merchant,
- wait for additional discriminatory evidence.

Be cautious around destructive recommendations.

For example:

> disabling an entire payment method

can have much larger business consequences than the original incident.

Do not automatically execute recommendations.

The TAM remains in control.

---

# 24. UI goal

The UI is important, but it is not the core intelligence.

The demo dashboard should make the system understandable within seconds.

Likely high-value views:

### Business overview

- current conversion
- normal/expected conversion
- GMV
- estimated GMV at risk
- active incidents
- merchant health

### Incident queue

Ordered by business priority.

### Incident detail

- affected dimensions
- timeline
- impact
- confirmed facts
- leading hypothesis
- confidence
- evidence
- competing explanations
- missing evidence
- recommended action

### Historical context

Enough history to demonstrate:

- normal behavior,
- anomaly onset,
- impact evolution,
- previous incident patterns.

Do not build a giant BI platform.

---

# 25. Four-minute demo philosophy

The complete experience needs to fit into approximately four minutes.

The strongest demo sequence is likely:

### 0. Establish normal world

Show multiple merchants generating realistic live traffic.

### 1. Judge fires hidden incident

No team intervention.

### 2. Detector reacts

Control Tower identifies the affected traffic.

### 3. Business impact appears

Priority changes based on monetary consequences.

### 4. Investigation starts automatically

Agent gathers evidence.

### 5. Diagnosis/inference appears

Specific, evidence-backed, uncertainty-aware.

### 6. Critical escalation fires

Phone call if severity warrants it.

### 7. Judge inspects evidence

Show exactly why the system believes what it believes.

The wow factor should come from:

> “It figured that out by itself.”

not from visual complexity.

---

# 26. Required test scenarios

Create deterministic test scenarios before polishing the demo.

At minimum cover:

### Scenario 1 — Provider degradation

A provider fails across several cohorts.

Expected diagnosis:

provider-level issue.

### Scenario 2 — Issuer-specific over-decline

One issuing bank degrades while provider behaves normally elsewhere.

Expected diagnosis:

issuer-specific issue.

### Scenario 3 — Country × method interaction

A payment method fails only in one country.

Expected diagnosis:

interaction, not global method outage.

### Scenario 4 — Fine-grained unseen combination

Example:

```text
merchant
× provider
× card network
× country
× issuer
```

The detector must discover the affected slice without a hard-coded rule for that combination.

### Scenario 5 — Retry amplification

Underlying failure causes:

- queue buildup,
- repeated attempts,
- elevated request volume.

System must avoid counting every retry as an independent customer loss.

### Scenario 6 — Normal traffic spike

Large increase in volume but no meaningful conversion degradation.

Expected:

no unnecessary high-priority incident.

### Scenario 7 — High-impact small conversion change

Small percentage shift on a huge merchant.

Expected:

high business priority.

### Scenario 8 — Dramatic low-volume anomaly

Large percentage change on negligible traffic.

Expected:

lower business priority.

### Scenario 9 — Ambiguous evidence

Provider and issuer are observationally confounded.

Expected:

bounded inference + explicit uncertainty + missing-evidence request.

### Scenario 10 — Multiple simultaneous incidents

Two unrelated failures.

Expected:

separate incidents.

### Scenario 11 — Infrastructure/deployment problem

Payment symptoms plus runtime/service evidence.

Expected:

investigation moves beyond payment dimensions.

### Scenario 12 — External source disagreement

Internal telemetry shows degradation while external status claims healthy.

Expected:

internal evidence remains authoritative.

---

# 27. Hidden test harness

Build an incident-injection harness where a scenario can be selected without telling the Control Tower its meaning.

Conceptually:

```text
inject incident configuration
        ↓
simulator changes behavior
        ↓
Control Tower sees only resulting telemetry
        ↓
diagnosis produced
        ↓
evaluator compares diagnosis to hidden ground truth
```

This harness is strategically important.

It lets us demonstrate generalization and prevents the project from becoming a scripted demo.

---

# 28. Development order

Do not start with UI polish.

Recommended dependency order:

## Phase 1 — Contracts and simulated world

Establish:

- payment identity,
- attempt identity,
- event contract,
- merchant profiles,
- baseline generation,
- incident injection,
- hidden truth.

Acceptance:

We can run normal traffic and inject known failures.

---

## Phase 2 — Deterministic observability

Establish:

- aggregation,
- conversion measurement,
- retry-aware metrics,
- cohort slicing,
- baseline comparisons,
- financial calculations.

Acceptance:

We can objectively show where behavior changed without an LLM.

---

## Phase 3 — Incident engine

Establish:

- anomaly creation,
- incident qualification,
- priority,
- persistence,
- multi-incident state.

Acceptance:

Normal noise does not spam incidents and high-impact degradation rises quickly.

---

## Phase 4 — Investigation harness

Establish:

- evidence retrieval,
- hypothesis evaluation,
- LLM investigation,
- external corroboration,
- uncertainty behavior,
- recommendations.

Acceptance:

Agent explanations cite observable evidence and can say “not enough evidence.”

---

## Phase 5 — End-to-end judge harness

Establish:

```text
trigger incident
→ detect
→ investigate
→ explain
→ escalate
```

Acceptance:

Zero manual intervention.

---

## Phase 6 — UI and phone escalation

Only once the core loop works reliably.

---

## Phase 7 — Demo hardening

Run the hidden test suite repeatedly.

Optimize:

- reliability,
- latency where needed,
- explanation clarity,
- graceful failure,
- demo pacing.

---

# 29. Reliability rules

This is a hackathon.

Prefer:

- deterministic startup,
- reproducible data,
- deterministic scenario seeds,
- explicit health checks,
- simple process boundaries,
- graceful degradation.

Avoid:

- fragile distributed complexity,
- unnecessary cloud dependencies,
- exotic infrastructure,
- unbounded agent loops,
- integrations that fail the whole product when offline.

If an external status service is unavailable:

> continue diagnosis without it.

If Slack fails:

> dashboard still works.

If phone escalation fails:

> incident diagnosis still works.

The core must never depend on a peripheral integration.

---

# 30. Scope discipline

Do **not** add features because they sound impressive.

Explicitly out of core demo scope:

- autonomous remediation,
- email notifications,
- user-owned ChatGPT integration,
- massive merchant counts,
- generic chatbot functionality,
- unrelated analytics,
- enterprise auth,
- sophisticated account management,
- unnecessary infrastructure abstractions.

The project wins on:

**detection → localization → evidence → impact → investigation → recommendation.**

Protect that loop.

---

# 31. Primary success criterion

At any point during implementation ask:

> If a judge triggers a degradation combination we did not explicitly encode as an incident type, can the system locate it, quantify its impact, investigate it, explain its evidence, represent uncertainty honestly, and recommend the next action without human assistance?

If the answer becomes weaker after a proposed change, reject the change.

---

# 32. Orchestration behavior

Start by converting this handoff into an executable dependency graph.

Parallelize work only when interfaces are sufficiently stable.

Maintain one canonical definition for:

- event contract,
- incident object,
- simulator ground truth,
- detector output,
- investigation input/output.

Do not allow separate workers to invent incompatible versions of these contracts.

Prioritize vertical integration over isolated completeness.

A rough but functional:

```text
simulator
→ detector
→ incident
→ investigator
→ UI
```

is more valuable early than five individually polished components that do not communicate.

Continuously run end-to-end validation as components land.

---

# 33. First orchestration task

Before dispatching implementation, produce a concise execution plan containing:

1. the minimal end-to-end vertical slice,
2. module boundaries,
3. shared contracts that must be frozen first,
4. tasks that can safely run in parallel,
5. dependency order,
6. estimated critical path,
7. explicit scope cuts for the 24-hour limit,
8. end-to-end acceptance test for each phase,
9. major technical risks,
10. fallback plan if the agent investigation or external integrations prove unreliable.

Then begin implementation.

Do not restart product discovery unless you encounter a genuine contradiction or blocker.

The target is a **working, defensible Control Tower**, not an overengineered platform.
