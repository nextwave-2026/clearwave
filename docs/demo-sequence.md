# ClearWave judge demonstration runbook

This is the rehearsal script for the seven-minute Control Tower pitch: about three minutes of explanation, followed by about four minutes in which the judges operate the product themselves.

**The judge uses a browser only.** The captain does not hand over a terminal, ask a judge to run a command, or take the keyboard back. The stack, its data, and its credentials are prepared before the pitch. Commands in this document are captain-only setup or recovery.

All merchants, banks, payments, incidents and outages shown are simulated data produced by this project's simulator. Nothing shown represents or implies a real incident, outage or service problem at any named company. Real company names are used only to make the demonstration recognisable and realistic.

## The one-sentence claim

ClearWave compares a merchant's cohorts with that merchant's recent behaviour and sibling cohorts, warns while a deviation is still developing, investigates without paging, and escalates the same record only when deterministic detection says it is an incident. It does not use a seasonal baseline and does not know recurring time patterns.

## Captain pre-flight

Do this off screen, preferably at least **60 minutes before the pitch**, and leave the stack running. The history preparation itself takes seconds: it writes eight event-time hours of healthy traffic so the live detector does not start cold. The extra lead time gives the live stack time to settle and gives the captain time to recover without putting that work in front of the judges.

From the repository root:

```sh
make stack-up
make stack-status
```

`make stack-up` is the clean start. It replaces `state/clearwave.db`, prepares healthy history, then starts Kafka, Schema Registry, all three workers, the detector daemon, the investigation daemon and the dashboard. Open the printed URL: **http://127.0.0.1:8082/**.

The exact pre-flight check is:

- `make stack-status` reports `broker: healthy` and `schema-registry: healthy`.
- Its `workers:` line reports merchant-a, merchant-b and merchant-c running; `detector:` and `investigation:` also report running.
- Its `dashboard:` line reports the dashboard answering at `http://127.0.0.1:8082/api/overview`.
- The detection baseline is warm: the measured clean start is **481 buckets / about 5,777 attempts** for `merchant-b` / `adyen`, against a requirement of 60 buckets.
- The merchant-relative floor is warm separately: **about 8.01 hours / about 11,549 payments**, against 6 hours / 200 payments.
- The browser opens on **Revenue impact**, showing **No revenue at risk**, `active_incident_count: 0`, and no `Watching` rows.

For the final quiet check, leave the healthy stack untouched for the verifier's **120-second** quiet observation. The expected result is zero incident rows, zero watches and zero active incidents. The captain can do that before the judges arrive; do not spend the judges' four minutes proving the quiet window.

Confirm the model key and Slack webhook are available without printing either secret. Slack delivery is part of the verified collapse beat. Do not add phone credentials to the primary path: a phone call is not a required beat of this rehearsal.

If any pre-flight condition fails, do not start the live sequence. Use the recovery plan below or the offline fallback, both off screen.

## Judge-operated sequence

The three masthead controls are the only controls the judge needs:

- **Developing deviation** publishes a decline probability of **0.12**.
- **Collapse** publishes a decline probability of **0.95**.
- **Clear** stops the introduced deviation.

Both stages target the one named demo input, `{merchant_id: merchant-b, provider: adyen}`, with the worker's `provider_timeout` decline shape. This is a control input, not a scenario identifier. The detector receives only the resulting payment traffic.

### 0. Start on healthy traffic

**What the judge clicks**

Nothing yet. Hand over with the browser already on **Revenue impact**. Invite the judge to look at the healthy board, then point to the masthead controls without pressing one.

**What should appear**

The board says **No revenue at risk**. It shows no active incidents and no watch rail. The provenance strip identifies SQLite and `CLEARWAVE_DB`; the traffic is live simulator traffic, not a pre-created incident.

**What the captain says**

> "This is a clean, warm store. The board has no incident and no watch. You will change the traffic with these controls; the detector and investigator will do the rest."

**What it proves**

Healthy traffic stays quiet. The baseline exists before the action, and the judge is operating the input rather than selecting a hidden diagnosis.

**If it does not happen**

- If the dashboard does not load, say: "The product surface is not answering, so I will not call an empty screen healthy." Refresh once. If it still fails, abandon the live path and use the prepared offline evidence tour.
- If an incident or watch is already present, do not explain it away as live detection. Read the browser's source and state, then restart the stack off screen with `make stack-up`. If there is not time, use the offline fallback.
- Do not delete rows or edit the database in front of the judges. That would invalidate the clean-start claim.

### 1. Start the developing deviation

**What the judge clicks**

The judge clicks **Developing deviation** in the masthead.

**What should appear**

The status text immediately says that the judge started a developing deviation in merchant-b's traffic on provider adyen. The board continues polling the same store. Within the verifier's **240-second** stage-one allowance, a single **Watching** row should appear for the affected cohort, with:

- a stored projected loss per hour labelled **if this continues**;
- the cohort shown as merchant-b / adyen;
- the watch reasons and detection-floor chips;
- a worsening trajectory; and
- no incident in the queue and no active-incident count increase.

A measured example was **USD 2,711.21/hour** against a typical hourly attempted value of **USD 45,950.68**. That is an example from a measured run, not a number to promise on stage; live figures are copied from the current record.

**What the captain says while waiting**

> "You have changed the input. The detector is measuring event-time buckets against merchant-b's recent behaviour and its siblings. It is not deciding that this is an outage yet, and the model is not deciding that either."

When the rail appears:

> "This is a watch, not an incident. It gives the operator an early, merchant-relative warning and shows which floors are still open."

**What it proves**

A developing deviation is visible before the incident floors are crossed. The projected loss is useful preventive advice, not realised loss. The watch is a C3 row in the same lifecycle, not a fabricated warning card and not a page.

**If it does not happen**

- First check the judge status text in the browser. If it says Kafka could not be reached, say: "The control did not deliver the input, so this is not evidence about detection." Do not claim that an incident fired. Move to the prepared fallback.
- If the status confirms delivery but no watch appears after 240 seconds, allow at most one extra 45-second detector sweep if the judges are still engaged. Say: "The input was delivered, but the measured contrast has not produced the watch in this window; I will not relabel silence as success." Then abandon this step and continue with the prepared static evidence tour rather than debugging live.
- If an incident appears immediately, call it what the board calls it. Do not call it a watch. The 0.12 stimulus or the store conditions did not produce the intended near-miss; press **Clear** only if needed, and move to recovery.
- If an unrelated watch appears, do not attribute it to the judge's action. Restart the clean stack off screen if time allows; otherwise do not use that row as evidence for this sequence.

### 2. Show prevention and the no-page boundary

**What the judge clicks**

After the watch appears, the judge clicks **Escalation** in the view bar, then returns to **Revenue impact**. No further trigger is pressed.

**What should appear**

The watch remains in the quiet **Watching** rail, outside the incident queue and outside the active-incident figures. The Escalation view says that no incident has escalated yet. No Slack message, phone call or pending call is recorded.

The investigation daemon claims a watch and can write a C4 result while the lifecycle remains `watching`; it returns the row to `watching` and does not page. The browser's primary watch rail exposes the deterministic preventive advice - projected impact, trajectory and the floors - rather than a causal diagnosis.

**What the captain says**

> "The system is investigating this while it is still only a watch, so a TAM has something to act on before the cliff. It deliberately does not page: a near-miss is worth attention on the board, but waking Slack or a phone before the incident floors are crossed would turn an early warning into alert noise."

If the wait is visible:

> "The board is polling the store. The worker, detector and investigator are doing work; the screen is not inventing a conclusion while they do it."

**What it proves**

Prevention is a real lifecycle, not a lower severity incident. A watch can receive investigation and advice without becoming an incident, and the no-page rule is structural.

**If it does not happen**

- If Escalation shows a channel or pending call for the watch, do not proceed as if that is expected. Say: "That is a routing failure, not a page I will present as correct." Use the static fallback and record the finding.
- If the watch disappears before the judge sees it, say: "The current measurement no longer supports the watch, so the detector withdrew it rather than leaving stale advice." If the rail disappears immediately after the button press, abandon the live sequence; do not claim prevention was shown.
- If the judge asks to open the watch's full causal narrative, be direct: "The primary board shows the watch evidence and advice; the detailed C4 view is available after the record becomes an incident." Do not imply the watch rail is a model diagnosis.

### 3. Escalate the same record by collapsing the deviation

**What the judge clicks**

The judge clicks **Collapse** in the masthead.

**What should appear**

The status text says the judge started the collapse in merchant-b's merchant-b / adyen traffic. The board keeps measuring. Allow up to the verifier's **480-second** stage-two window; a measured end-to-end run produced diagnosis in about **113.5 seconds** after the collapse control.

When the floors are crossed:

- the watch becomes the incident on the **same incident id**;
- the affected cohort sharpens from `{merchant_id: merchant-b, provider: adyen}` to include the observed country, for example `{merchant_id: merchant-b, provider: adyen, country: CO}`;
- the record enters the incident queue and Revenue impact shows the stored financial impact;
- the investigation result appears in Incident detail with a leading hypothesis, alternatives, confidence and recommended next action; and
- the Escalation view records the Slack delivery for the stored severity.

Click the incident row in **Incident queue**, then show **Incident detail** and **Evidence trail**. Point to the incident id first, before discussing the sharper cohort. The queue's severity and the investigation's diagnostic confidence are separate fields.

**What the captain says while waiting**

> "You have supplied the second stage. Detection still decides when the data crosses its fixed floors; the model is not deciding whether it is an incident. Once it is detected, the investigator queries the evidence surface and writes a bounded explanation."

When the row appears:

> "This is the same record, now sharper because the measured drop is larger. It has moved from early warning to incident, and Slack is a consequence of the stored severity - not of the model's confidence."

At the detail view:

> "These are observations, alternatives and the next investigation step. The figures came from executed evidence queries; the narrative does not get to make up a number."

**What it proves**

The system escalates a real, sustained deviation rather than the judge's button press. One cohort keeps one record across watch and incident. Detection is early and merchant-relative, investigation is evidence-bound, and Slack routing follows deterministic business priority.

**If it does not happen**

- If the collapse status reports that Kafka is unreachable, say: "The second input did not deliver, so I will not claim a collapse." Keep the watch visible if it is still true, then move to the static fallback.
- If no incident appears after 480 seconds, inspect the browser only: if the watch is present, say that the sustained detection floors were not crossed in the demonstration window; if it is absent, say that the deviation recovered or was withdrawn. Do not call either state a successful incident. Show the watch rail or the healthy board and move on.
- If an incident appears but its investigation is still running, open Incident detail. The board should say that the investigation is running, while localisation, money and the record remain available. Say: "The deterministic result is here; the bounded narrative has not completed yet." Do not wait indefinitely or invent the narrative.
- If the incident has no narrative because the model is unavailable, show the evidence trail if present and say: "The agent failed closed. The incident, money and executed evidence remain; only the narrative is unavailable." Continue to the clear step if the record is live.
- If the stage-two id differs from the stage-one id, do not say "same record." Say: "The detector produced a new record, so the identity guarantee is not demonstrated on this run." Show the available stored record and move on.
- If Slack is `not_configured` or `failed`, say exactly that. The dashboard is still a valid stored incident surface, but external Slack delivery was not observed. Do not imply delivery from the Escalation binding diagram.

### 4. Clear and recover

**What the judge clicks**

The judge clicks **Clear** in the masthead.

**What should appear**

The status text says the judge cleared the introduced deviation. Allow up to the verifier's **120-second** clear window and the detector's next sweep. Traffic returns toward its healthy shape. The active-incident count returns to zero and the watch rail is empty. The earlier record may remain as closed history; the board must not present its old loss rate as money still running.

**What the captain says while waiting**

> "You cleared the input; ClearWave is not remediating anything. The worker changes because you pressed Clear. The detector observes recovery, and the board stops presenting the open exposure when the stored lifecycle says it is no longer active."

When healthy:

> "The loop is closed: healthy traffic, watch, investigation, incident, and recovery. The system advised a human; it did not reroute or change production on its own."

**What it proves**

The system can clear stale preventive state, recover the board to no active exposure, and preserve the prior record as history without claiming that historical money is still at risk.

**If it does not happen**

- If the clear status says Kafka was unreachable, say: "The clear command did not deliver, so the traffic was not changed by this press." Do not claim recovery. Continue with the incident detail and evidence trail, then end the demo honestly.
- If the worker status is successful but the board still shows an active row after 120 seconds, say: "The input was cleared, but the detection sweep has not closed this record in the available window." Do not hide the row or call it healthy. Move to the recovery fallback.
- If the watch rail remains but the incident is gone, keep waiting only until the 120-second allowance. A stale watch is not a healthy result; say so and stop debugging in front of judges.
- If Revenue impact says no active exposure but a closed merchant card says **Was costing / hour** or **Was at risk**, that is expected history. Point out the past tense. If it says **Loss rate** or **Revenue at risk** for a closed source, do not present the screen as recovered; use the static fallback.

## Captain-only recovery fallback

The primary path is live and browser-operated. Recovery commands are never given to judges.

1. **Dashboard down or stack not warm:** refresh once. If it still fails, check `make stack-status` off screen. Restart with `make stack-up` only if there is enough time to let the clean start complete. Do not run a second ad hoc detector or point the dashboard at a different SQLite file.
2. **Broker or worker down:** do not press a control and narrate a result. The status response distinguishes `delivered: false` from a delivered command. Say the input did not fire and use the offline evidence tour.
3. **Model unavailable:** the deterministic incident is still valid. Show the incident's cohort, change, money and evidence trail, and say the narrative is unavailable. Never substitute a hand-written cause.
4. **Slack unavailable:** show the stored dashboard incident and the Escalation binding. Say Slack was not configured or delivery failed. A binding is not proof of delivery.
5. **No time to recover:** use a prepared offline store and dashboard only as a static evidence tour. Label it as offline and precomputed. It demonstrates measurement, investigation and citations, but it does **not** demonstrate a live judge-triggered watch or live recovery.

The broker-free fallback is for the captain's preparation, not the judge's primary interaction:

```sh
.venv/bin/python -m detector seed && .venv/bin/python -m detector detect
.venv/bin/python -m investigation.vertical --investigate-only --db state/clearwave.db
```

Use the same `CLEARWAVE_DB=state/clearwave.db` for every process. Do not run the fallback over the live store or mix it with the live stack. Do not use `--mode anomaly`; it is not a supported flag.

## Technical defence notes

### If ClearWave does not understand seasonality yet, why is it valuable to Yuno today?

**Executive answer:** ClearWave turns silent conversion loss into an early, merchant-specific warning, a localised route and an evidence-bound diagnosis with the money attached, so a TAM spends the incident acting on evidence instead of crossing filters at 3 a.m.

**Operations answer:** The detector compares current event-time buckets with each cohort's trailing hour and sibling cohorts, writes a non-paging watch while a deviation develops, and uses fixed rules for incident status and severity; only that deterministic state can trigger escalation. The bounded investigator starts with cohort comparison, service health, decline-code mix, retries, confounding and financial impact; it can add only allowlisted queries, and every factual or causal claim, including every figure, must cite an executed query. If the evidence cannot separate causes, it returns `ambiguous` or `insufficient_evidence` with the missing observation instead of manufacturing certainty.

**Direct answer:** ClearWave detects deviations from recent merchant behaviour and sibling cohorts. It does not yet distinguish an expected recurring spike from an abnormal one purely because it has learned that time pattern. That still covers abrupt, commercially serious route-specific failures: same-window divergence localises the problem, while cohorts moving together keep us from blaming one provider. It does not protect against a recurring route-specific pattern or tell whether a shared merchant-wide movement is normal for Friday; closing that gap requires an hour-of-week baseline built from replayable multi-week merchant and cohort history, with sparse-cell shrinkage, missing-data and holiday handling, and backtests proving it reduces false positives without hiding real incidents.

### What is deterministic, and what is not?

**Deterministic code decides:**

- ingestion and normalisation into one canonical event shape;
- event-time bucketing behind the lateness watermark;
- the trailing baseline and parent/sibling comparisons;
- the cohort-localisation path and its stop rule;
- the watch and incident floors;
- the financial calculations written to C3;
- severity from business-impact inputs; and
- escalation routing and paging from the stored severity and lifecycle.

The fixed incident floors are a two-proportion z-score at or below **-3**, an absolute conversion drop of at least **0.02**, at least **30 attempted payments**, and **3 sustained buckets**. The watch is deliberately earlier: z-score at or below **-1.5**, an absolute drop of at least **0.01**, enough volume, and a worsening trajectory, while the incident floors are still open. The clauses are conjunctive; one weak signal is not enough.

The live control itself is deterministic too: it targets merchant-b / adyen and publishes the selected stage. That fixed target is the demo input, not a leak of hidden truth. The detector never receives a scenario name, cause, intended magnitude or ground-truth record.

**The non-deterministic boundary is investigation.** The model can choose among the allowlisted evidence tools and write the C4 narrative or a typed answer. Its run is bounded. Tool responses, query ids, citations and all figures are still from the deterministic evidence gateway and the shared store.

### What does the model decide, and what does it not decide?

The model writes the investigation narrative: confirmed facts, a leading hypothesis, competing explanations, uncertainty, missing evidence and a recommended next action. It can answer a typed business question through the same evidence gateway.

It does **not** decide whether traffic is anomalous, what cohort is reported, what severity is assigned, whether Slack or a phone should fire, or what any number is. Those are code paths before and after the model.

Every figure in a model answer must be tied to a query id from a query that actually executed. The gateway validates that citation. An asserted number without an executed citation is a failed answer, not a number the UI should quietly trust.

### Why is a watch preventive?

A watch is raised before the four incident floors are crossed. It is investigated while its lifecycle is still `watching`, and it can carry projected impact, leading indicators, trajectory and the floors that remain open. It intentionally does not page.

Not paging is a feature: the operator gets lead time on the board without turning every near-miss into an interruptive Slack or phone alert. C5 refuses a watch by lifecycle allowlist before it reads severity, claims a channel or fires a side effect. The watch severity is also forced to `low` as defence in depth. When the floors later pass, detection upgrades the same row rather than opening a second record.

### How do evidence and uncertainty work?

The investigator receives measured C2 responses, not hidden simulator state. A weak or structurally inseparable result stays weak. The result can say that evidence does not establish a cause, list competing explanations, identify why they cannot be separated, and name the observation that would discriminate them.

That is intentional failure behaviour. A confident sentence is not a successful diagnosis if the evidence does not support it. Diagnostic confidence describes causal evidence; it is independent of business severity.

### How is business impact represented?

`gmv_at_risk` and `loss_per_hour` are deterministic figures from the measured incident window. The separate `projected_loss_per_hour` on a watch is:

> the measured conversion shortfall applied to that cohort's typical hourly attempted value from the trailing window.

It means what an hour at the currently measured rate would cost **if it continues**. It is **not money already lost**. It is not a platform total, and it never ranks severity. Severity uses the stored business-impact inputs and its fixed ceilings; it does not read the watch's projected figure as a paging instruction.

Retries are counted as attempts where appropriate but not as new customer payments for payment-level conversion or value. Currency conversion uses the frozen reporting table, so replaying the same events does not silently change the answer.

### What does ClearWave deliberately not automate?

It does not remediate, reroute, change provider configuration, or act on an operator's behalf. It tells a human what is happening, how much is exposed, what the evidence supports, and what to investigate next.

That is the right boundary for payments. An incorrect automatic reroute can amplify a provider or issuer problem, violate a merchant's routing policy, or create a second outage. A human owns the production action; ClearWave supplies measured context and an auditable recommendation.

### What are the honest limits?

The current baseline is a trailing window on the same merchant and its sibling cohorts. There is **no seasonal baseline**. ClearWave does not know that Friday nights differ from Tuesday mornings and must never claim that it does.

Adding that capability would require replayable historical data, an hour-of-week baseline implementation, enough history per merchant and cohort, missing-data and holiday handling, and a validation plan proving that seasonal context reduces false positives without hiding real incidents. That is future work, not a property of this demo.

The demo is also simulated, compose-based infrastructure rather than a production deployment. External corroboration is optional, and a provider status page cannot prove causality. The system can say what the observed payment evidence establishes and what it does not.

## What stays out of the demo

These items are explicitly **not part of the primary path**:

- **Any terminal or command operated by a judge.** The judge only clicks the browser controls and view tabs.
- **The phone call.** The current binding is dashboard for low/medium, dashboard plus Slack for high, and dashboard plus Slack plus phone for critical. A phone requires correctly configured Twilio credentials and a critical stored severity; it is not needed to prove this sequence and was not the verified collapse beat.
- **Ask the data.** It is an explicit model request, not part of the four-minute flow. Do not add a second model wait while the judge is watching the lifecycle.
- **A full causal narrative on the watch rail.** The watch rail's visible advice is deterministic projected impact, trajectory and floors. The detailed investigation surface is shown after the record becomes an incident; do not imply that the rail itself is a model diagnosis.
- **The three hidden scenario names or the ground-truth evaluator.** The judge sees the observable effect, never a scenario catalogue. Evaluator scoring is post-run validation, not a judge control.
- **A seasonal, hour-of-week or Friday-night claim.** None exists in the current baseline.
- **Exact live money or severity promises.** Quote only figures on the current stored record. A measured example is not a guarantee; severity belongs to the detector.
- **Rerouting, remediation or automatic action.** The recommended next action is advice only. No production system is changed by ClearWave.
- **A mixed live/offline store.** Every live consumer uses the one `CLEARWAVE_DB` file. An offline seed or a second dashboard pointed at another file is a different demonstration and must be labelled as such.

Primary references: [`docs/prd.md`](prd.md), [`scripts/verify_demo.py`](../scripts/verify_demo.py), [`surfaces/inject.py`](../surfaces/inject.py), [`docs/contracts/incident.md`](contracts/incident.md), [`docs/contracts/investigation-result.md`](contracts/investigation-result.md), and [`docs/contracts/notification-escalation.md`](contracts/notification-escalation.md).
