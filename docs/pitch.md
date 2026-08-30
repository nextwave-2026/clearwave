# Pitch

> **Delivery plan:** About three minutes speaking, then hand the system to the judges for the demo and trial. Keep the remaining time for questions. The organisers publish both seven-minute and ten-minute formats, so rehearse the seven-minute version. ([challenge format](challenge.md#judging-format), [team timing decision](../DECISIONS.md))

## The problem

Conversion is the share of attempted payments that are approved. In payment orchestration, one aggregate conversion number can hide very different failures: a provider can degrade, one issuing bank can over-decline, or one payment method can fail in one country.

The person who feels this is the Technical Account Manager responsible for merchants. A broad alert only says that something moved. The TAM still has to cross merchant, provider, method, country, issuer, decline, retry, and operational evidence to find where it moved and why. The challenge describes that work as a tired human crossing filters at 3 a.m. It also describes the failure at both ends: noisy alerts are ignored, while conservative alerts miss the incident until hours have passed. ([challenge problem](challenge.md#the-problem), [product mission](prd.md#1-mission))

This is not just an anomaly problem. A dramatic percentage change on eight payments can matter less than a small conversion loss on a high-volume merchant. The operational question is: where is the degradation, what is the business exposure, what evidence separates the possible causes, and what should the TAM investigate next? ([anomaly versus incident](prd.md#10-anomaly-versus-incident))

## Why this matters to the organising companies

For a payments company, the link is direct. The challenge places the platform between merchants and multiple payment providers. The platform can observe attempts across those routes, but useful operations work requires turning that visibility into a localized, priced, evidence-backed incident. ([challenge scenario](challenge.md#scenario))

For a logistics company, the relevant point is the operating pattern, not a claim about its internal systems. Clearwave separates observable events, deterministic detection, bounded investigation, and human-owned action. That pattern can be evaluated for another operational stream without pretending that payment dimensions or thresholds transfer unchanged. We are not asserting anything about Nauta's architecture, data, or current operations.

For both judges, the defensible claim is narrow: this repository demonstrates one working Control Tower for simulated payment operations. It does not claim production deployment or internal knowledge of either organising company.

## What we built

Clearwave has five stages.

1. Simulated merchants publish payment attempts, payment closures, and operational telemetry through Kafka.
2. The detection plane normalizes the events, stores them in one SQLite evidence store, measures conversion, qualifies a sustained deviation, localizes the affected cohort, and prices the impact.
3. It writes a deterministic incident. No model is used to decide whether traffic is anomalous.
4. A bounded OpenAI investigation queries only the published evidence tools. It returns confirmed facts, a leading hypothesis, competing explanations, missing evidence, diagnostic confidence, and a recommended action.
5. The dashboard and escalation channels render those records. They do not recompute business facts. The system recommends. It never remediates production automatically. ([end-to-end flow](demo-sequence.md#end-to-end-flow), [ownership boundaries](ownership.md#the-four-workstreams))

The important choices are the ones that protect the answer under pressure.

**First, money bounds severity.** We rejected a simple weighted score. In that design, persistence and trajectory could eventually promote a cheap incident. Clearwave uses money as both an input and a ceiling, while still accounting for blast radius, persistence, and trajectory. A small percentage loss on a large merchant can therefore outrank a dramatic low-volume drop. All money is calculated per customer payment, never per retry attempt, and labelled GMV at risk rather than platform revenue. ([ADR 0016](adr/0016-severity-is-bounded-by-money.md), [ADR 0019](adr/0019-value-is-priced-per-payment.md))

**Second, severity and diagnostic confidence never collapse into one score.** Severity answers how urgently the business should respond. Confidence answers how well the evidence separates causes. `CRITICAL` with `LOW` confidence is valid. We rejected a combined score because uncertainty about cause must not hide a large economic incident. ([ADR 0002](adr/0002-diagnostic-confidence-belongs-to-investigation.md), [product rule](prd.md#11-severity-and-confidence-are-independent))

**Third, uncertainty is structural, not a writing style.** Whether provider and issuer are confounded is computed from a deterministic cross-tabulation. A hypothesis can be ruled out only with cited contradictory evidence. Every citation is a query identifier issued by the evidence gateway, not by the model. We rejected asking the model to notice confounding because honest uncertainty cannot depend on model attention. ([ADR 0005](adr/0005-confounding-detection-is-deterministic.md), [ADR 0006](adr/0006-evidence-gateway-owns-query-identity-and-calls.md), [ADR 0007](adr/0007-ruled-out-hypotheses-require-contradiction.md))

**Fourth, hidden truth is quarantined.** Scenario identifiers never enter detection or investigation. The simulator changes only observable behavior. The evaluator sees hidden truth after diagnosis. We rejected scenario-aware prompts because they would turn the trial into a scripted answer. ([C6 boundary](../INTERFACES.md), [ADR 0012](adr/0012-scenario-identifiers-never-reach-l4.md))

**Finally, replay must mean the same thing.** Detection buckets on event time behind a lateness watermark, not consumer arrival time. Native decline codes map to one closed vocabulary while the original provider code is preserved for escalation. We rejected arrival-time buckets because broker lag would change the answer, and rejected free-text decline reasons because comparable evidence would disappear into near-synonyms. ([ADR 0018](adr/0018-event-time-bucketing.md), [ADR 0021](adr/0021-canonical-vocabulary-with-preserved-raw-code.md))

## Live demonstration

All merchants, banks, payments, incidents, and outages shown are simulated by this repository. No screen represents or implies a real incident at any named company.

### Safe stage path

This is the path to use unless Kafka is already healthy before the pitch. The exact commands are in the [operator runbook](demo-sequence.md#operator-runbook---copy-paste-this-under-pressure).

Start with an empty SQLite file and the dashboard pointed at that file. The screen should show zero incidents. In the second terminal, use `.venv/bin/python -m investigation.vertical --db "$DB"`. Do not use system Python. Wait for `Lifecycle after investigate: diagnosed`.

The recorded default run produced a critical incident on `{provider: provider-p2}`. Conversion moved from `0.849744` to `0.52`. The system estimated USD `1,648.72` GMV at risk and USD `19,784.62` per hour. Investigation returned `outcome=ambiguous`, `diagnostic_confidence=medium`, and a narrative. The dashboard read one diagnosed incident from the same store. ([recorded stage result](demo-sequence.md#1-stage-path-cold-checkout-to-a-diagnosed-incident-on-screen))

An earlier deterministic rehearsal of all three guaranteed scenarios took two minutes forty-eight seconds. The evaluator returned cohort precision `1.0` and recall `1.0` for each scenario. Do not repeat three fresh model calls on stage: model calls have varied from about 45 seconds to more than 100 seconds. Use the prepared stage path and preserve time for the judge. ([demo standing](demo-sequence.md#which-path-to-use), [evaluator pass rule](../evaluator/README.md))

Expected result: the judge can inspect what changed, the affected cohort, event-time onset, GMV at risk, severity, diagnostic confidence, competing explanations, recommended action, and the evidence query trail. If the model is unavailable, the incident, localization, money, and evidence still render. Only the narrative is marked `agent_unavailable`. ([ADR 0010](adr/0010-every-investigation-emits-a-result.md))

The dashboard trigger is not part of this offline path. With no broker, it reports `delivered: false` instead of pretending an incident fired.

### Live Kafka path

Use this only when Kafka, Schema Registry, all three compose workers, the dashboard, and the shared database are already running. A worker must be publishing before the judge toggles the incident. Control consumers start from the latest message, so an earlier command is silently lost. After injection, allow roughly three minutes of consumption for sustained contrast. One minute was not enough in the observed run. ([live path](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not))

The proven run consumed `4,254` records with `0` rejected. The judge toggle drove merchant-b and Adyen conversion from `88.5%` to `0.0%`, `5.9%`, and `6.1%`. Detection stored incident `inc-2026-08-30-715ab9c3` on `{merchant-b, adyen, CO}` with USD `3.89` GMV at risk. After the toggle was turned off, conversion recovered to `96.7%`, `97.1%`, and `85.7%`, and the next sweep returned `incident: null`. ([verified live run](../STATUS.md))

If the broker, workers, or timing are not ready, stop the live attempt and run the offline path. Do not debug Docker during the pitch. Do not use `make live` as the opening: it starts neither Kafka nor a worker and does not guarantee an incident.

## What comes next

The next work is evidence-driven hardening, not a new feature list.

- **Replace the trailing-window baseline with the contextual hour-of-week profile.** The 15-day, 100,000-event backfill now streams into the shared store, but the detector still uses the simpler baseline. The deciding evidence is a replay across time-of-day and weekend patterns showing fewer false positives without missing the three guaranteed incidents. ([detection next steps](detection-plane.md#current-state-and-what-is-next), [backfill measurement](../STATUS.md))
- **Add a supported command to investigate an already-detected store.** Today `detector detect` stops after C3, while `investigation.vertical` reseeds even with `--keep`. The deciding evidence is an end-to-end run where a judge-created live C3 record is claimed and diagnosed without reseeding or writing Python on stage. ([known CLI gap](demo-sequence.md#there-is-no-command-that-investigates-an-already-detected-store))
- **Close the live evaluator access gap without weakening quarantine.** In the compose path, hidden truth remains inside worker containers and the host evaluator cannot read it. The deciding evidence is a live scored run where only the evaluator can access C6 after diagnosis, while detection and investigation demonstrably cannot. ([recorded open risk](../STATUS.md), [C6 contract](contracts/hidden-truth.md))
- **Settle the remaining evidence seams.** `payments.closed` is stored but does not feed measurement, and C2/C3 expose no highest-value payment identifier for direct TAM lookup. The deciding evidence is a replay showing one authoritative payment-level answer and a contract-reviewed lookup that surfaces a real payment identifier without creating a second aggregation path. ([current detection state](detection-plane.md#current-state-and-what-is-next))
- **Measure investigation behavior over repeated hidden runs.** Model latency and price are variable. One maximum-query run took about 55 seconds and roughly USD `0.02`; that is an observation, not a guarantee. Repeated latency, outcome, citation-validity, and evaluator results would determine whether the present query and time budgets should change. ([ADR 0022](adr/0022-openai-structured-loop-over-coding-agent-harness.md))

## Anticipated questions

### Why use a model at all? Why not make the entire system deterministic?

Detection, localization, confounding, severity, and financial impact are already deterministic. The model is limited to comparing explanations, identifying missing evidence, and writing a TAM-facing recommendation over measured facts. A no-model alternative was rejected because rules for those interpretations would tend to become scenario-specific. The model has no shell, filesystem, raw-event, evaluator, or remediation access. ([ADR 0022](adr/0022-openai-structured-loop-over-coding-agent-harness.md), [`investigation/agent.py`](../investigation/agent.py))

### How do you know the model did not invent the diagnosis or cite a query that never ran?

The evidence gateway is the sole C2 caller and assigns every query identifier. The adapter checks citations against the executed trail and rejects unsupported identifiers, claims without evidence, a mismatched incident ID, or a severity field in the investigation result. A ruled-out hypothesis also needs cited contradictory evidence. ([ADR 0006](adr/0006-evidence-gateway-owns-query-identity-and-calls.md), [ADR 0007](adr/0007-ruled-out-hypotheses-require-contradiction.md), [`investigation/gateway.py`](../investigation/gateway.py))

### What happens with late events, duplicate Kafka delivery, or a replay?

Detection uses event time behind a watermark. Sealed windows are not silently rewritten by late arrivals. Kafka offsets advance only after the SQLite write is durable, and event tables deduplicate on `event_id`, so a crash can replay a batch without counting it twice. This trades late correction for stable, reproducible incident history during the current build. ([ADR 0018](adr/0018-event-time-bucketing.md), [C1b boundary](../INTERFACES.md), [`detector/consumer.py`](../detector/consumer.py))

### Can Clearwave really handle a combination that was not programmed in advance?

The localization rule does not encode a scenario or fixed dimension order. It descends only when one child is materially worse than its siblings, and it stops at the most specific cohort supported by contrast and volume. Regression tests cover both a provider-country intersection and a provider-wide failure. The honest limit is that only three scenarios are guaranteed and rehearsed, and the current dashboard toggle fires one fixed provider-degradation shape. A successful hidden run on a new judge-selected combination is the evidence that would prove the broader claim live. ([ADR 0017](adr/0017-localisation-descends-on-contrast.md), [scenario scope](scenarios.md), [`tests/test_detector.py`](../tests/test_detector.py))

### Is the live trial fully scored end to end today?

No. The live Kafka path has been proven through injection, consumption, detection, storage, recovery, and dashboard rendering. The compose workers' hidden-truth databases are not exposed to the host evaluator, so that live run cannot currently receive an evaluator verdict. The offline evaluator path works, and quarantine is preserved. We prefer that honest limitation to mounting hidden truth where detection or investigation could reach it. ([live evaluator limit](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not), [C6 boundary](../INTERFACES.md))
