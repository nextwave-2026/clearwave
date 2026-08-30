# Pitch

> **Seven-minute run of show:** 3:00 spoken, 4:00 judge-operated trial. Rehearse to the section budgets below. The seven-minute total and judge handoff are settled in [`DECISIONS.md`](../DECISIONS.md).

## The problem

> **0:35**

Conversion is the share of attempted payments that are approved. One aggregate number can hide a provider degradation, one issuing bank over-declining, or one method failing in one country.

The Technical Account Manager feels that ambiguity. A broad alert says something moved, but the TAM still crosses merchant, provider, method, country, issuer, decline, retry, and operational evidence to learn where and why. The challenge describes a tired human doing that at 3 a.m. Noisy alerts are ignored. Conservative alerts arrive hours late. ([challenge problem](challenge.md#the-problem), [product mission](prd.md#1-mission))

## Why this matters to the organising companies

> **0:25**

For a payments company, the link is direct: turn visibility across merchant-provider routes into a localized, priced, evidence-backed incident. For a logistics company, the transferable idea is the separation of observable events, deterministic detection, bounded investigation, and human-owned action. We make no claim about either organiser's internal systems. This is a working Control Tower for simulated payment operations. ([challenge scenario](challenge.md#scenario))

## What we built

> **1:30**

Clearwave runs five stages. Simulated merchants publish payment and operational events through Kafka. A deterministic plane normalizes them into one SQLite evidence store, detects a sustained conversion loss, localizes the cohort, prices GMV at risk, and assigns severity. A bounded OpenAI investigation can query only published evidence tools. It returns facts, competing explanations, missing evidence, confidence, and a recommended action. The dashboard renders those records without recomputing them. Clearwave recommends. It never remediates automatically. ([end-to-end flow](demo-sequence.md#end-to-end-flow), [ownership](ownership.md#the-four-workstreams))

Five choices protect the answer under pressure.

- Money is both an input to severity and a ceiling on it. We rejected a weighted score that could promote a cheap but persistent incident. Money is calculated per customer payment, never per retry attempt, and labelled GMV at risk. ([ADR 0016](adr/0016-severity-is-bounded-by-money.md), [ADR 0019](adr/0019-value-is-priced-per-payment.md))
- Severity and diagnostic confidence never collapse into one score. `CRITICAL` with `LOW` confidence is valid. Uncertainty about cause must not hide economic urgency. ([ADR 0002](adr/0002-diagnostic-confidence-belongs-to-investigation.md))
- Confounding is a deterministic cross-tabulation. The model can rule out a cause only with cited contradictory evidence, and only the gateway can issue a citation ID. ([ADRs 0005-0007](adr/0005-confounding-detection-is-deterministic.md))
- Hidden truth and scenario identifiers never reach detection or investigation. We rejected scenario-aware prompts because they would script the trial. ([C6](../INTERFACES.md), [ADR 0012](adr/0012-scenario-identifiers-never-reach-l4.md))
- Detection uses event time behind a watermark, not broker arrival time. Decline reasons use one comparable vocabulary while preserving the provider's raw code. ([ADR 0018](adr/0018-event-time-bucketing.md), [ADR 0021](adr/0021-canonical-vocabulary-with-preserved-raw-code.md))

Now the judges take the controls.

## Live demonstration

> **4:00, operated by the judges. Do not narrate over them.**

All names and incidents shown are simulated demo data. Nothing implies a real incident at a named company.

### Preflight before the clock

Have these ready before speaking:

- Kafka, Schema Registry, all three compose workers, and the dashboard are healthy.
- The 100,000-event backfill is loaded into the same `CLEARWAVE_DB` used by detection and the dashboard.
- Browser tab 1 shows the live database with zero active incidents.
- Terminal 1 contains a 180-second live consume command, not yet submitted.
- Terminal 2 contains the injection command below, not yet submitted.
- Browser tab 2 contains the proven offline diagnosed incident as the fallback.

Do not use `make live` as setup. It starts neither Kafka nor a worker. A worker must publish before injection because the control topic starts from latest. ([operator runbook](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not))

### 0:00-0:30 - Judge chooses an unrehearsed cohort

Hand the judge Terminal 2 and this choice card:

| Merchant | Safe provider choices |
| --- | --- |
| `merchant-a` | `stripe`, `dlocal` |
| `merchant-b` | `adyen`, `mercadopago` |
| `merchant-c` | `stripe`, `adyen`, `dlocal`, `mercadopago` |

The judge changes only the merchant and provider in this command, then presses Enter:

```sh
.venv/bin/python -m worker.inject merchant-c --provider dlocal --effect decline
```

This is an unrehearsed merchant-provider pairing, not a scenario identifier. Expected: the terminal prints `published to incidents.control`, then the chosen worker logs `incident control: now targeting ... with effect=decline`. The judge immediately submits Terminal 1:

```sh
CLEARWAVE_DB=state/clearwave.db .venv/bin/python -m detector consume --seconds 180 --detect
```

([`worker.inject`](../worker/inject.py))

**Fallback:** If publish is not acknowledged or no worker reacts within ten seconds, say that the command did not fire. Hand the judge the dashboard toggle. It fires the proven fixed `merchant-b`/`adyen` degradation. If that also reports `delivered: false`, stop the live path and open Browser tab 2.

### 0:30-3:20 - Judge watches detection

The judge keeps the live dashboard open while the 180-second consumer completes. The incident needs sustained event-time contrast, so one minute is not enough.

Expected for a qualifying cohort: a C3 incident appears with the localized dimensions, expected and actual conversion, onset, GMV at risk, severity, and lifecycle state. The last proven toggle run consumed `4,254` records with `0` rejected, moved conversion from `88.5%` to `0.0%`, `5.9%`, and `6.1%`, and stored `{merchant-b, adyen, CO}` at USD `3.89` GMV at risk. Those are rehearsal figures, not promises for the judge's new choice. ([verified run](../STATUS.md))

A new narrow cohort can honestly return `incident: null` if it does not meet the statistical, absolute-drop, volume, and persistence floors. Do not rename that as success. Open Browser tab 2 so the judges can still inspect the proven diagnosed path. ([ADR 0015](adr/0015-detection-floors-not-a-single-threshold.md))

### 3:20-4:00 - Judge inspects evidence and restores traffic

If the live C3 exists, the judge opens it and checks that the screen matches the stored cohort, money, and severity. The current supported CLI cannot investigate an already-detected live store, so do not claim a live C4 diagnosis that did not run.

Then the judge uses Browser tab 2 to inspect the proven offline C4 result: leading and competing explanations, qualitative confidence, recommended action, and gateway-issued evidence IDs. The recorded offline run produced a critical `{provider: provider-p2}` incident, conversion `0.849744 -> 0.52`, USD `1,648.72` GMV at risk, and an ambiguous medium-confidence diagnosis. ([safe stage result](demo-sequence.md#1-stage-path-cold-checkout-to-a-diagnosed-incident-on-screen))

Finally, the judge restores the selected worker:

```sh
.venv/bin/python -m worker.inject merchant-c --stop
```

Use the merchant they selected. Expected: the worker logs `cleared active incident`. If the live path failed earlier, there is nothing to stop.

The broker-free fallback is proven. An earlier rehearsal of all three guaranteed scenarios took `2:48`; the evaluator returned cohort precision and recall of `1.0` for each. Model calls varied from about 45 to more than 100 seconds, so never start three fresh calls inside this slot. If the model is unavailable, deterministic localization, money, and evidence remain visible while the narrative is marked `agent_unavailable`. ([demo standing](demo-sequence.md#which-path-to-use), [ADR 0010](adr/0010-every-investigation-emits-a-result.md), [evaluator rule](../evaluator/README.md))

## What comes next

> **0:30, spoken after the trial**

The highest-value next step is a supported command that claims and investigates an already-detected live store without reseeding. Then we can prove the judge's injection through C4, not only C3. After that: evaluate the 15-day backfill as an hour-of-week baseline, and give the live evaluator isolated access to hidden truth without exposing it to detection or investigation. The evidence required is one live, judge-created, fully scored run with the quarantine intact. ([known CLI gap](demo-sequence.md#there-is-no-command-that-investigates-an-already-detected-store), [open evaluator risk](../STATUS.md), [detection next steps](detection-plane.md#current-state-and-what-is-next))

## Anticipated questions

### Why use a model at all?

Detection, localization, confounding, severity, and money are deterministic. The model only compares explanations, identifies missing evidence, and recommends a human action over measured facts. It has no shell, filesystem, raw-event, evaluator, or remediation access. A no-model alternative was rejected because those interpretation rules would tend to become scenario-specific. ([ADR 0022](adr/0022-openai-structured-loop-over-coding-agent-harness.md), [`investigation/agent.py`](../investigation/agent.py))

### How do you stop the model inventing evidence?

The evidence gateway is the sole C2 caller and assigns every query ID. The adapter checks citations against the executed trail and rejects unsupported IDs, uncited claims, a mismatched incident ID, or severity in the investigation result. A ruled-out hypothesis needs cited contradiction. ([ADR 0006](adr/0006-evidence-gateway-owns-query-identity-and-calls.md), [ADR 0007](adr/0007-ruled-out-hypotheses-require-contradiction.md), [`investigation/gateway.py`](../investigation/gateway.py))

### What happens with late events, duplicate delivery, or replay?

Buckets use event time behind a watermark. Sealed windows are not silently rewritten. Kafka offsets advance only after the SQLite write is durable, and tables deduplicate on `event_id`. A crash can replay a batch without counting it twice. The accepted trade-off is stable replay over retroactive correction of sealed windows. ([ADR 0018](adr/0018-event-time-bucketing.md), [C1b](../INTERFACES.md), [`detector/consumer.py`](../detector/consumer.py))

### Can it handle a combination that was not programmed?

Localization has no scenario or fixed dimension order. It descends only when one child is materially worse than its siblings and stops at the most specific supported cohort. Tests cover provider-country and provider-wide failures. The honest limit is that only three scenarios are guaranteed and the browser toggle fires one fixed shape. The judge command can choose a different merchant-provider pairing, but a successful hidden run is the evidence for the broader claim. ([ADR 0017](adr/0017-localisation-descends-on-contrast.md), [scenario scope](scenarios.md), [`tests/test_detector.py`](../tests/test_detector.py))

### Is the judge's live incident diagnosed and scored end to end today?

No. Live injection, Kafka consumption, detection, storage, recovery, and dashboard rendering are proven. There is no supported investigate-existing-store command, and compose hidden truth is unavailable to the host evaluator. The offline diagnosis and evaluator work, with quarantine preserved. We state that gap rather than joining unrelated results into a false end-to-end claim. ([live limits](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not), [C6](../INTERFACES.md))
