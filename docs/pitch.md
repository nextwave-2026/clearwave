# Pitch

> **Seven-minute run of show:** 3:00 spoken, 4:00 judge-operated trial. Rehearse to the section budgets below. The seven-minute total and judge handoff are settled in [`DECISIONS.md`](../DECISIONS.md).

## The problem

> **0:40**

Conversion is the share of attempted payments that are approved. One aggregate number can hide a provider degradation, one issuing bank over-declining, or one method failing in one country.

The Technical Account Manager feels that as noise or as silence. Classic alerts fire on everything, or on nothing. By the time the merchant calls, the incident is already expensive.

We heard the sharper version of that from you, not from a slide.

A Yuno engineer told us the highest-value work is finding subtle edge cases and root causes, not detecting incidents and applying local patches. The large failures come from accumulated bandage fixes.

A Yuno product manager told us they do not need another system that says something is broken. They need to know early that something abnormal is developing, why it is suspicious for that specific merchant, and what to prepare to do.

That is the problem we built around.

## Why this matters to the organising companies

> **0:20**

For a payments company, the link is direct: merchant-relative visibility, priced, investigated, early enough to act. For a logistics company, the transferable idea is the split of observable events, deterministic detection, bounded investigation, and human-owned action. We make no claim about either organiser's internal systems. This is a working Control Tower for simulated payment operations. ([challenge scenario](challenge.md#scenario))

## What we built

> **1:30**

Clearwave compares a cohort to its own recent behaviour, not to a platform average. Expected conversion is the last hour of that same slice, shrunk toward the parent when volume is thin. When localisation has fixed a merchant, that number is that merchant's last hour. It is not a weekly profile. It is not seasonal. We will not tell you we know Friday nights. We do not.

Five stages. Simulated merchants publish payment and operational events through Kafka. A deterministic plane normalizes them into one SQLite evidence store, compares the current window to that merchant-relative baseline, localizes the cohort, prices GMV at risk, and assigns severity from business impact. A bounded OpenAI investigation can query only published evidence tools. It returns facts, competing explanations, missing evidence, confidence, and a recommended action. The dashboard renders those records without recomputing them. Clearwave recommends. It never remediates automatically. ([end-to-end flow](demo-sequence.md#end-to-end-flow), [ownership](ownership.md#the-four-workstreams))

That is already more than a better threshold. Merchant A at seventy percent is not compared to Merchant B at ninety-five. A volume spike that holds conversion stays silent. Localisation walks six dimensions without a scenario catalogue, and stops when a child is not materially worse than its siblings. Confounding is a cross-tab, not a guess.

The honesty about early warning. Today's detector still has two outputs: silence, or a fully qualified incident. A developing six or seven point drop is already measured. The floors that stop alert fatigue then discard it. That is the gap a watch on the same record is meant to close: unusual for this merchant against its last hour, not yet an incident, not paged. If a watching row is on the board, that is it, and the warning and the incident are the same row. If it is not, the floors still waited for the conventional failure, and we will say so. We do not predict. We do not forecast a collapse in eleven minutes. ([detection baseline](detection-plane.md#l2---the-baseline), [ADR 0024](adr/0024-leading-indicators-warn-early-without-prediction.md))

Investigation is the other half of what you asked for. It does not apply a bandage. It compares explanations, cites evidence, and says when it cannot tell. Severity and diagnostic confidence never collapse into one score. `CRITICAL` with `LOW` confidence is valid. Uncertainty about cause must not hide economic urgency.

A daemon watches the store. When an incident is detected, diagnosis appears without anyone typing. Notifications fire once, and only after that diagnosis exists. Two overlapping reads cannot double-page. If the model is down, the placeholder narrative is not shown as if it were a real diagnosis.

Five choices protect the answer under pressure.

- Money is a term and a ceiling, so a cheap grind cannot page you on persistence alone. Yuno's product owners named the failure of applying that ceiling as the same dollar numbers to every merchant: it misses high-volume, low-ticket merchants. We recorded the redesign, merchant-relative ceiling, recurrence may promote. Confirm the band on screen. Do not quote a band from an earlier rehearsal. ([ADR 0023](adr/0023-severity-is-relative-to-the-merchant-and-promoted-by-recurrence.md))
- Severity and diagnostic confidence never collapse into one score. ([ADR 0002](adr/0002-diagnostic-confidence-belongs-to-investigation.md))
- Confounding is a deterministic cross-tabulation. The model can rule out a cause only with cited contradictory evidence, and only the gateway can issue a citation ID. ([ADRs 0005-0007](adr/0005-confounding-detection-is-deterministic.md))
- Hidden truth and scenario identifiers never reach detection or investigation. We rejected scenario-aware prompts because they would script the trial. ([C6](../INTERFACES.md), [ADR 0012](adr/0012-scenario-identifiers-never-reach-l4.md))
- Detection uses event time behind a watermark, not broker arrival time. Decline reasons use one comparable vocabulary while preserving the provider's raw code. ([ADR 0018](adr/0018-event-time-bucketing.md), [ADR 0021](adr/0021-canonical-vocabulary-with-preserved-raw-code.md))

Now the judges take the controls.

## Live demonstration

> **4:00, operated by the judges. Do not narrate over them.**

All names and incidents shown are simulated demo data. Nothing implies a real incident at a named company.

The beat to protect: two timestamps on one record, warning before the conventional failure. Everything else in the four minutes serves that. If the watch is not on this machine, skip to Path B. Do not fake a watch on a ninety-five percent cliff.

### Preflight before the clock

Have these ready before speaking:

- Kafka, Schema Registry, all three compose workers, the dashboard, and the investigation daemon are healthy, all pointed at `state/clearwave.db`. Create `state/` before `docker compose up`. Do not give the investigation service the ground-truth volume.
- Minutes of healthy traffic are already in that store. Consume a healthy baseline before you inject. A store that is already degraded returns `incident: null` correctly, because the trailing hour has no contrast. A consume without `--detect` should already be running against that file so injected traffic lands before the next detect sweep.
- Browser tab 1 shows the live dashboard with zero active incidents.
- Browser tab 2 contains the proven offline diagnosed incident as the fallback.
- Confirm Path A or Path B with this check. The dashboard must expose `Developing`, `Collapse`, and `Clear`, and a rehearsal detect must emit `lifecycle_state: watching`. Otherwise use Path B.

Do not use `make live` as setup. It starts neither Kafka nor a worker. A worker must publish before injection because the control topic starts from latest. The daemon is `.venv/bin/python -m investigation` or `make investigate-daemon`. If the daemon is not running, the typed fallback is `make investigate DB=state/clearwave.db`. ([operator runbook](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not), [investigate an already-detected store](demo-sequence.md#investigating-a-store-that-is-already-detected-against))

### Path A - live two-stage, only if preflight confirmed watching

### 0:00-0:20 - Judge fires a mild deviation

The judge clicks `Developing` in the dashboard. It publishes the mild decline for the fixed live target (`merchant-b` / `adyen`) through W1's `worker.inject`; no scenario identifier crosses the boundary.

**Fallback:** If the dashboard reports `delivered: false`, say that the command did not fire. Stop the live path and open Browser tab 2.

### 0:20-1:20 - Judge watches the warning

The judge keeps the live dashboard open. The compose detector sweeps the same store every 45 seconds.

Expected: a watching row on the chosen cohort. Unusual for this merchant against its last hour. Not an incident. No Slack. No phone. No model. The daemon must not claim it.

If no watching row appears after the expected sweep window, say so. Move to the collapse. Do not rename silence as success.

Spoken line before the next command, then stop talking: the merchant has not called. We are not paging you. We are telling you this is unusual for this merchant against its own hour, and it is getting worse.

### 1:20-1:30 - Judge fires the collapse

The judge clicks `Collapse` in the dashboard. This keeps the same live target and changes only the injected magnitude.

### 1:30-3:20 - Judge watches detection and diagnosis

Expected: the same record upgrades to `detected` on a detector sweep. The daemon claims it. Diagnosis appears with nobody typing. Notifications fire once, after that diagnosis exists.

The screen must show two times on one record: watching first, detected later. That is the beat.

A new narrow cohort can honestly return `incident: null` if it does not meet the statistical, absolute-drop, and volume floors. Do not rename that as success. Open Browser tab 2. ([ADR 0015](adr/0015-detection-floors-not-a-single-threshold.md))

**Fallback if the daemon is silent:** `CLEARWAVE_DB=state/clearwave.db .venv/bin/python -m investigation.vertical --investigate-only`. It never seeds, never detects, and never resets the store.

### 3:20-4:00 - Judge inspects evidence and restores traffic

The judge opens the record and checks that expected conversion is this cohort's trailing hour, not a static threshold. Then leading and competing explanations, qualitative confidence, recommended action, and gateway-issued evidence IDs. Then the judge clicks `Clear`. Expected: the dashboard acknowledges the clear command and the worker logs `cleared active incident`. If the live path failed earlier, there is nothing to stop.

### Path B - proven live inject, no watch

Use this if preflight failed the watching check, or if Path A went silent.

### 0:00-0:30 - Judge fires the proven collapse

The judge clicks `Collapse` in the dashboard. The running detector service consumes and sweeps continuously; nobody submits a terminal command during the judge-operated path.

Same publish fallback as Path A: if the dashboard reports `delivered: false`, open Browser tab 2.

### 0:30-3:20 - Judge watches detection

The incident needs sustained event-time contrast, so one minute is not enough. Three minutes of consume after a healthy baseline is the proven window.

Expected for a qualifying cohort: a C3 incident with the localized dimensions, expected and actual conversion, onset, GMV at risk, and lifecycle state. Expected is this cohort's last hour. `baseline_method` is `trailing_window_with_parent_shrinkage`. Do not quote a severity band from an earlier run. The band on screen is the one this run produces.

A later proven toggle run consumed `4,254` records with `0` rejected and moved conversion from `88.5%` to `0.0%`, `5.9%`, and `6.1%` on `{merchant-b, adyen, CO}`, priced at USD `3.89` GMV at risk. Those are rehearsal figures, not promises for the judge's new choice. ([verified run](../STATUS.md))

If the daemon is up, diagnosis may appear before the four minutes end. Model calls have run from about 45 seconds to more than 100. A measured daemon run moved detected to diagnosed in 65 seconds. Do not promise the low end.

### 3:20-4:00 - Judge inspects evidence and restores traffic

If a live C4 exists, the judge opens it. If it does not, the judge inspects the live C3, then Browser tab 2 for the proven offline C4. The typed join onto a live store is `--investigate-only` if you still have time and the daemon was not running.

The recorded offline seed path produced a `{provider: provider-p2}` incident, conversion `0.849744 -> 0.52`, USD `1,648.72` GMV at risk, baseline method `trailing_window_with_parent_shrinkage`. Do not quote its band as a guarantee. ([safe stage result](demo-sequence.md#1-stage-path-cold-checkout-to-a-diagnosed-incident-on-screen))

Then restore with `--stop` on the merchant they selected.

The broker-free fallback is proven end to end for the provider degradation path: `detector seed --scenario provider_incident` stores a C3, and `investigation.vertical` produces the diagnosed C4 and dashboard view. The other two guaranteed scenarios are not exposed through supported offline seed commands and are not promised here. The earlier all-three rehearsal used hand-written `tests.synthetic` generators, so its timing and evaluator result are not guarantees for the supported seed path. Never start three fresh model calls inside this slot. If the model is unavailable, deterministic localization, money, and evidence remain visible while the narrative is marked `agent_unavailable` and is not rendered as a diagnosis. ([demo standing](demo-sequence.md#which-path-to-use), [ADR 0010](adr/0010-every-investigation-emits-a-result.md), [evaluator rule](../evaluator/README.md))

If two offline snapshots were prepared, one watching and one detected, they are the named fallback for the two-timestamp beat when live two-stage slips. Swap the dashboard from the first store to the second. Do not fire the hard cliff and call it a watch.

## What comes next

> **0:30, spoken after the trial**

The architecture already named the next baseline: an hour-of-week profile, now that replayable history can be ingested. Today the contextual primitive is this merchant's last hour. We have not learned Friday nights.

Then a leading-indicator watch on latency, timeouts, and retries, the sequence that precedes conversion collapse, still with no forecast. That is [ADR 0024](adr/0024-leading-indicators-warn-early-without-prediction.md). It is a decision, not shipped behaviour.

Then `payment_integrity`: payload, lifecycle, and raw-code smells the conversion trigger cannot see. Deferred this freeze on purpose.

On scale: ingest is not the wall. Localisation is. Same 60,000 rows, 4.1 seconds at demo cardinality, 113.2 seconds at 200 merchants, 491.4 seconds at 2,000. That last figure is a correction of an earlier abandoned-run claim. The cheap fix is to apply the volume floor before enumerating, not after evaluating. It is not built. ([scaling](scaling.md))

## Anticipated questions

### How do you know what normal is for a merchant you have only just started observing?

The trailing hour of that cohort, shrunk toward the parent when volume is thin. If there is no history, we do not invent a profile. The floors keep us quiet rather than guessing. We do not fully solve cold start. Seasonal hour-of-week is the designed next step, gated on replayable history that actually has weekly shape. The fifteen-day file is not in the repository and is not on the demo machine, so no seasonal baseline exists today. ([`detector/detect.py`](../detector/detect.py), [`detector/config.py`](../detector/config.py))

### How is this different from alerting with better thresholds, which you have already tried?

A threshold is a global number. This expected value is this merchant, this cohort, this last hour. Localisation has no scenario and no fixed dimension order. Investigation then compares causes over cited evidence instead of paging a bandage. A watch, if present, is a weaker claim than an incident: not claimed, not escalated. That is the opposite of loosening the floors until everything fires. ([ADR 0015](adr/0015-detection-floors-not-a-single-threshold.md), [ADR 0017](adr/0017-localisation-descends-on-contrast.md), [ADR 0024](adr/0024-leading-indicators-warn-early-without-prediction.md))

### What stops it crying wolf?

Three numeric floors plus a measurement check: z, absolute drop, volume. Low-volume cells borrow the parent's rate. A child enters the cohort only when it is materially worse than its siblings. Volume spikes that hold conversion stay silent. A watch must never page. Notifications fire once, after a diagnosis exists, behind an atomic claim. We do not fully solve alert fatigue. A loose watch predicate would recreate it on the dashboard even without Slack. ([ADR 0015](adr/0015-detection-floors-not-a-single-threshold.md), [`surfaces/store.py`](../surfaces/store.py))

### How do you avoid a confident wrong diagnosis?

The evidence gateway is the sole C2 caller and assigns every query ID. Citations must match the executed trail. A ruled-out hypothesis needs cited contradiction. Competing explanations stay on the record. Confidence is qualitative and independent of severity. The model cannot see hidden truth. We do not fully solve a wrong but well-cited story. What we refuse is an uncited one, and a placeholder rendered as if it were a diagnosis. ([ADR 0006](adr/0006-evidence-gateway-owns-query-identity-and-calls.md), [ADR 0007](adr/0007-ruled-out-hypotheses-require-contradiction.md), [`investigation/gateway.py`](../investigation/gateway.py))

### What happens when the model is unavailable?

The incident stays. Localisation, money, and evidence stay on screen. The narrative is `agent_unavailable` and is not shown as a diagnosis. The daemon still runs; without a key it uses the unavailable client rather than crashing. Notifications can still fire on that degraded result, because every investigation emits one. ([ADR 0010](adr/0010-every-investigation-emits-a-result.md), [`investigation/degrade.py`](../investigation/degrade.py))

### Why use a model at all?

Detection, localization, confounding, severity, and money are deterministic. The model only compares explanations, identifies missing evidence, and recommends a human action over measured facts. It has no shell, filesystem, raw-event, evaluator, or remediation access. A no-model alternative was rejected because those interpretation rules would tend to become scenario-specific. ([ADR 0022](adr/0022-openai-structured-loop-over-coding-agent-harness.md), [`investigation/agent.py`](../investigation/agent.py))

### How do you stop the model inventing evidence?

The evidence gateway is the sole C2 caller and assigns every query ID. The adapter checks citations against the executed trail and rejects unsupported IDs, uncited claims, a mismatched incident ID, or severity in the investigation result. A ruled-out hypothesis needs cited contradiction. ([ADR 0006](adr/0006-evidence-gateway-owns-query-identity-and-calls.md), [ADR 0007](adr/0007-ruled-out-hypotheses-require-contradiction.md), [`investigation/gateway.py`](../investigation/gateway.py))

### What happens with late events, duplicate delivery, or replay?

Buckets use event time behind a watermark. Sealed windows are not silently rewritten. Kafka offsets advance only after the SQLite write is durable, and tables deduplicate on `event_id`. A crash can replay a batch without counting it twice. The accepted trade-off is stable replay over retroactive correction of sealed windows. ([ADR 0018](adr/0018-event-time-bucketing.md), [C1b](../INTERFACES.md), [`detector/consumer.py`](../detector/consumer.py))

### Can it handle a combination that was not programmed?

Localization has no scenario or fixed dimension order. It descends only when one child is materially worse than its siblings and stops at the most specific supported cohort. Tests cover provider-country and provider-wide failures. The honest limit is that only three scenarios are guaranteed and the browser toggle fires one fixed shape. The judge command can choose a different merchant-provider pairing, but a successful hidden run is the evidence for the broader claim. ([ADR 0017](adr/0017-localisation-descends-on-contrast.md), [scenario scope](scenarios.md), [`tests/test_detector.py`](../tests/test_detector.py))

### Does this survive two million payments a day?

Ingest does. Measured at about 25,000 accepted rows per second against a requirement near 30. The wall is cohort search: `localise()` enumerates every distinct value ever stored, then evaluates, then applies the volume floor. Same 60,000 rows: 4.1 seconds at demo cardinality, 113.2 seconds at 200 merchants, 491.4 seconds at 2,000. That 491.4 is a public correction of an earlier "abandoned after 20 minutes" line. The cheap fix is to bound enumeration by cohorts active in the window. It is not built. Payrails trained one model per merchant-provider pair and declined to go finer by country. Our live run localised to `{country: CO, merchant_id: merchant-b, provider: adyen}`, which includes the axis they declined. Their models learn seasonality. Ours do not. ([scaling](scaling.md))

### Is the judge's live incident diagnosed and scored end to end today?

Diagnosed: yes, if the daemon is pointed at the same store, or if someone runs `--investigate-only`. Live injection, Kafka consumption, detection, storage, recovery, dashboard rendering, and investigation of an already-detected store are proven. Scored: only when a scenario worker writes C6 onto the host volume. Compose healthy-traffic workers do not. The offline diagnosis and evaluator work, with quarantine preserved. We state that gap rather than joining unrelated results into a false end-to-end claim. ([live limits](demo-sequence.md#live-kafka-path---what-genuinely-works-and-what-does-not), [C6](../INTERFACES.md))
