# Decisions

Append only. Never edit or delete an existing entry. Correct a past decision by appending a new decision. Keep the newest entries at the bottom.

Each entry is exactly two lines. The first carries an ISO 8601 UTC timestamp, **who made the decision** (your GitHub username, `derek`, `andres`, `juank`, or `raul`), and what changed. The second states what every other contributor must now do differently - that line is the reason this file exists, so never leave it out. The literal `-> other side:` prefix means every other contributor, not one specific counterpart.

**Commit this file straight to `main`** - no branch, no pull request. If your push is rejected because
another contributor pushed first, run `git pull --no-rebase && git push`; union merge keeps both entries.

## Entries

- 2026-08-26T23:43Z  derek  schedule: the tooling site governs, the terms document is the stale side
  -> other side: plan Saturday morning against 09:00 check-in, not 10:00, and re-check the site Friday night - it has been revised once already and Luma still embeds the removed Friday start
- 2026-08-26T23:43Z  derek  pitch: budget the 7 minutes as roughly 3 speaking, 4 with judges operating it themselves
  -> other side: rehearse the handover into the demo until it is quick and boring, and do not take the keyboard back - trial by fire means unrehearsed input from them, not a driven walkthrough
- 2026-08-26T23:43Z  derek  quota: our model allowances are separate by design, so priming does not carry across
  -> other side: schedule your own usage-window priming before Saturday; mine covers only my account, and starting cold at T-ZERO costs you the first window
- 2026-08-26T23:43Z  derek  unattended work: gnhf is a conditional go, gated on a validation run before the event
  -> other side: if that run fails we drop it rather than debug it; if it passes, use it only for adversarial test-hardening of a working slice - never features, never the demo branch, never auto-push, and bound it by iteration count because the token counter under-reports
- 2026-08-26T23:43Z  derek  decision log: an agent may capture what was decided and when, but never the reasoning
  -> other side: make your agent ask you for the "because" and record your words - judges ask us about this log directly, and a rationale you did not actually give is what collapses under questioning
- 2026-08-26T23:43Z  derek  coordination skills: four are in flight in nextwave-kit and not yet in this repo
  -> other side: once they land here they fire automatically - claim before build, stop on an INTERFACES.md conflict rather than resolving it, capture decisions, and verify a working claim by testing it as a stranger would. Say now if you want any of them changed, before they are copied in
- 2026-08-26T23:43Z  derek  availability: I plan to be offline roughly 03:00-07:00 on Sunday
  -> other side: do not leave an interface decision pending across that gap, and expect no reply until 07:00 - with no messaging channel, a blocked question costs you the whole window
- 2026-08-29T16:52Z  derek  challenge: committed the team to Challenge 02, Control Tower; the pick is final under SYS.A and cannot be revisited
  -> other side: all work targets the Control Tower objective; the full brief is in `docs/challenge.md`
- 2026-08-29T16:52Z  derek  team: now four people; `juank` (Juan Camilo, GitHub `juank115`) and `raul` (Raul Higuera, GitHub `raulhiguerac`) added
  -> other side: every coordination entry must be attributed with the writer's handle (`derek`, `andres`, `juank`, or `raul`); the "two sides" model in older entries now means four contributors who may be on separate machines
- 2026-08-29T16:52Z  derek  stack and architecture remain deliberately open
  -> other side: nobody adds a package manifest, lockfile, Dockerfile, or application scaffold until a human decides the stack, and that decision gets its own entry here when it is made
- 2026-08-29T18:09Z  derek  baseline PRD accepted and published at \; it governs product direction
  -> other side: use \ as the product authority and correct disagreements by appending here
- 2026-08-29T18:09Z  derek  build divided into four workstreams W1-W4 with contract ownership in \
  -> other side: keep workstream boundaries and ownership legible from the repository
- 2026-08-29T18:09Z  derek  six contracts C1-C6 each have exactly one owning workstream; specifying C1-C4 is the gate on parallel work
  -> other side: do not open parallel implementation before the C1-C4 interface gate and a running stubbed path
- 2026-08-29T18:09Z  derek  severity is owned by the detection plane and diagnostic confidence by the investigation agent; they never collapse into one score
  -> other side: preserve independent severity and diagnostic confidence in every contract and surface
- 2026-08-29T18:09Z  derek  person-to-workstream assignment deliberately NOT decided and needs a human call
  -> other side: do not infer assignments from backgrounds or begin parallel ownership claims
- 2026-08-29T18:09Z  derek  baseline PRD accepted and published at `docs/prd.md`; it governs product direction
  -> other side: use `docs/prd.md` as the product authority and correct disagreements by appending here
- 2026-08-29T18:09Z  derek  build divided into four workstreams W1-W4 with contract ownership in `docs/ownership.md`
  -> other side: keep workstream boundaries and ownership legible from the repository
- 2026-08-29T18:11Z  derek  the two DECISIONS.md entries timestamped 2026-08-29T18:09Z whose paths render as bare backslashes were corrupted by a tooling error and are void, superseded by the correctly rendered entries of the same timestamp
  -> other side: disregard the backslash entries and read the entries with backticked paths
- 2026-08-29T18:15Z  derek  workstream assignment decided: W1 Simulated World and Ground Truth to raul, W2 Detection Plane to andres, W3 Investigation Agent to derek, W4 Surfaces and Escalation to juank
  -> other side: each person must read their workstream's Owns and Hard rule entries in docs/ownership.md and the contracts they own or consume; the stack decision is now the only remaining gate on starting parallel implementation
- 2026-08-29T18:16Z  derek  integration across the four workstreams is owned by derek alongside W3; each contributor's internal working method is deliberately unspecified while the seam rules bind everyone
  -> other side: preserve the documented seams and rules, while choosing your own internal workflow; integration ownership adds no authority over contract disputes

- 2026-08-29T18:26Z  derek  coordination instructions reconciled from a two-side arrangement to four contributors; all four handles are valid attribution
  -> other side: attribute entries with your own handle - `derek`, `andres`, `juank` or `raul`
- 2026-08-29T19:04Z  derek  ingestion: Kafka is the real ingestion pipeline; merchant producers publish to raw topics, and each simulator emits its own native heterogeneous event shape
  -> other side: publish and register each merchant's native shape on raw Kafka topics; do not replace the pipeline with a simulation prop or force producer shapes to match
- 2026-08-29T19:04Z  derek  merchant shapes: each simulated merchant emits its own native event shape; deliberate heterogeneity mirrors the real orchestrator problem
  -> other side: preserve native per-merchant differences and register each shape rather than flattening producers into a shared input shape
- 2026-08-29T19:04Z  derek  schema: a schema registry normalises heterogeneous merchant shapes into one canonical ingestion schema, serialised as JSON Schema for speed in the build window
  -> other side: register native shapes with W1 and consume the single JSON Schema canonical model downstream; do not introduce Avro or parallel canonical models
- 2026-08-29T19:04Z  derek  persistence: the normalized representation is persisted in a non-relational document store for detection, investigation, analytics and incident workflows; the specific product remains undecided
  -> other side: build every downstream workflow against the consistent document model without selecting or naming a document-store product yet
- 2026-08-29T19:04Z  derek  ownership: W1 (`raul`) owns raw per-merchant shapes and registration; W2 (`andres`) owns normalisation, the canonical schema and persistence, because the contract owner is whoever breaks when it is wrong
  -> other side: let W1 change merchant shapes without breaking three people, and route canonical-schema or persistence changes through W2
- 2026-08-29T19:04Z  derek  detection: detection reads the canonical topic for rolling aggregates and uses the document store as its query surface
  -> other side: keep rolling aggregation on the canonical stream and expose/query evidence through the document store rather than creating another read model
- 2026-08-29T19:04Z  derek  integration: the scenario catalogue and evaluator move from W1 to `derek` alongside integration because they are integration and validation concerns and this removes the largest critical-path concentration
  -> other side: W1 supplies hidden truth and injection, while `derek` owns the scenario catalogue, evaluator and integration validation
- 2026-08-29T19:04Z  derek  investigation: use headless `pi-coding-agent` (MIT); disable built-in shell, file-read, edit and write tools so only W2 evidence-query scripts are available; use non-interactive JSON print mode, wall-clock timeout, contract validation with one retry, then deterministic incident without narrative on invalid output; direct model-API tool-calling is the fallback
  -> other side: provide only evidence-query scripts to Pi, enforce the timeout and result contract, retry invalid JSON once, degrade without failing incidents, and keep the direct API loop as the documented fallback
- 2026-08-29T19:04Z  derek  detection quality: statistical detection is demo-grade; controlled scenarios and tuned thresholds are acceptable, but tune the sensitivity, never tune the search space - cohort localisation stays genuinely general across arbitrary dimension combinations
  -> other side: tune thresholds against controlled scenarios without narrowing cohort search dimensions; preserve general localisation as the defensible property
- 2026-08-29T19:17Z  derek  persistence: relational database, SQLite, supersedes the earlier persistence and detection entries; chosen for a simpler and more deterministic solution
  -> other side: treat the earlier persistence and detection entries as void and build persistence, detection, investigation, analytics and incident workflows against SQLite
- 2026-08-29T19:17Z  derek  deterministic scripts: evidence-query tools and detection-side scripts run on Python 3; this does not decide the language or framework for the rest of the stack
  -> other side: implement only the deterministic evidence-query and detection-side scripts in Python 3, and leave all other stack choices open
- 2026-08-29T19:17Z  derek  evaluator: diagnoses are scored automatically with structured overlap between diagnosed cohort dimensions and the injected ground-truth slice, using precision and recall; free-text matching is rejected as too brittle
  -> other side: emit structured cohort dimensions for evaluation and use precision and recall against the hidden slice, never a free-text match
- 2026-08-29T19:17Z  derek  demo scope: the guaranteed demo path has three scenarios - provider degradation, the observationally inseparable provider-versus-issuer confounder, and a high-impact small-percentage change on a large merchant; remaining scenarios stay documented without a build guarantee
  -> other side: guarantee and rehearse only those three scenarios for the demo, while retaining the other scenarios as documentation rather than committed demo scope
- 2026-08-29T19:17Z  derek  investigation failure: if the investigation agent fails at runtime, the incident remains visible with its localisation, financial impact and evidence rendered, while only the narrative is absent and explicitly marked unavailable
  -> other side: never drop an incident because the agent failed; render the available incident data and mark the missing narrative as unavailable
- 2026-08-29T19:38Z  andres  detection: a deviation qualifies as an incident only on a two-proportion test against a contextual baseline plus three floors - minimum absolute drop, minimum cohort volume, and sustained across consecutive buckets - so a tiny cohort cannot raise an incident on noise
  -> other side: expect no incident below those floors; sensitivity is tuned in versioned config, never by narrowing the cohort search space
- 2026-08-29T19:38Z  andres  severity: computed deterministically from business impact alone - log-scaled loss per hour, blast radius, persistence and trajectory - and never from statistical strength or evidence quality
  -> other side: never re-rank incidents by diagnostic confidence or by how strong the statistics look; a critical severity with low confidence is valid required output, and the severity components travel with every C3 record
- 2026-08-29T19:38Z  andres  time: all detection arithmetic buckets on event time behind a lateness watermark, never on arrival or wall-clock time, so replaying a recorded stream reproduces identical buckets, incidents and severity
  -> other side: put an event timestamp on every record you produce; the evaluator and any demo replay can rerun a scenario and get byte-identical detector output, and late events are counted rather than retro-fitted into a sealed window
- 2026-08-29T19:38Z  andres  canonical schema: C1b preserves payment identity, attempt identity and attempt number on every record, and normalises native provider decline codes into one closed vocabulary while carrying the raw provider code through unparsed
  -> other side: register native shapes with their own decline codes and W2 maps them; never collapse attempts into payments upstream, and never rely on free-text decline reasons surviving into the canonical model
- 2026-08-29T19:38Z  andres  build order: W2 lands a minimum operational backend first - the thinnest path that ingests, detects, prices, ranks and hands off - and deepens the functions and metrics only once that path is confirmed working
  -> other side: W2's first landed detector uses a trailing-window baseline and single-level localisation against the already-published C2 and C3 contracts; the seasonal baseline and deeper search arrive as later increments behind the same contracts, so build against the contracts and not against the first implementation
- 2026-08-29T19:43Z  derek  incident handoff: L4 consumes no Kafka; it polls the relational SQLite store for detected incidents, claims one, and moves it to diagnosed when the result is persisted
  -> other side: W2 must durably write every C3 record with `lifecycle_state: detected`; that state is the sole handoff signal, so W2 does not call L4
- 2026-08-29T19:43Z  derek  C2 proposal: add a `metric_series` evidence tool for one named metric and cohort over ordered event-time buckets behind the lateness watermark, because onset, severity trajectory and L4 narrative need a consistent trend
  -> other side: this is a proposal awaiting W2's acceptance; W2 owns C2 and may accept or reject it

- 2026-08-29T19:54Z  derek  investigation: L4 agent runtime is now a hand-rolled Python loop over the OpenAI Responses API, superseding the earlier Pi decision; Pi required a TypeScript tool bridge around Python subprocesses, and its continue-until-complete design opposed our bounded investigation
  -> other side: the earlier Pi entry is void; L4 will carry exactly two third-party dependencies, and no second language runtime enters the demo path
- 2026-08-29T20:17Z  andres  C2 contract: accept derek's `metric_series` proposal (STATUS.md 19:43Z) as an eleventh C2 tool, published separately rather than folded into `cohort_metrics`, because folding it in would change the response shape of a tool two other workstreams have already built against and rule 4 in `docs/ownership.md` keeps contract changes additive during the build window
  -> other side: consumers may rely on `metric_series` as its own tool alongside `cohort_metrics`, not as a field or mode of it; W2 publishes it in `docs/contracts/evidence-tools.md` and its implementation lands in a separate pull request

- 2026-08-29T18:39Z  raul  stack (W1 scope only): Python is W1's implementation language for the worker in `worker/`, exercising the per-workstream tooling freedom in `docs/ownership.md` ("Working method inside a workstream"). This does not decide the team-wide stack - `docs/ownership.md`'s Open Decisions still lists language/framework/stack as open, consistent with derek's 19:17Z entry scoping his own Python choice to W2's evidence-query scripts only. Rationale (raul's words): "python es simple jaja"
  -> other side: no team-wide prohibition lifts from this; W1's package manifest (`worker/requirements.txt`) and scaffold are scoped to W1's own tree only
- 2026-08-29T18:39Z  raul  transport: simulator-to-detector uses real Kafka with Schema Registry (not a simplified in-process alternative), resolving the open item in `docs/ownership.md`. Rationale (raul's words): "kafka por que es el estandar para una cola de mensajes y comunicación entre ms"
  -> other side: W2 (andres) consumes C1 events from Kafka via the schema registered in Schema Registry, not from an in-process call or a simplified queue; the running Kafka + Schema Registry from the devcontainer is the real transport, not a stand-in
- 2026-08-29T20:22Z  derek  external_status ownership: W3 (`derek`) implements `external_status`; it remains published as a C2 tool and ADR 0003 governs its shared gateway interface, not implementation ownership
  -> other side: W2 leaves `external_status` on its fixture and builds the other nine; C2 is an interface contract, not an implementation roster, so implementation ownership follows the data source
- 2026-08-29T20:22Z  derek  metric_series: acknowledged and accepted as an eleventh C2 tool rather than folded into `cohort_metrics`
  -> other side: W2 publishes and implements `metric_series`; W3 consumes it for onset and trajectory in the investigation narrative
- 2026-08-29T20:32Z  andres  C2 evidence store: the measured tools read one SQLite file located by the `CLEARWAVE_DB` environment variable, defaulting to `state/clearwave.db` relative to the working directory, and an absent store is created empty rather than treated as an error
  -> other side: set `CLEARWAVE_DB` once and the detector CLI, every C2 tool and your runner read the same file; never point a consumer at a second store, because two stores is the divergent-answer failure the architecture exists to prevent
- 2026-08-29T20:32Z  andres  C2 empty-store semantics: a tool with no measured data returns a well-formed honest response - zero counters, null where a rate is undefined, an empty list, and a stated reason when an incident is not stored - and never a crash, never a borrowed fixture number; the error envelope is reserved for malformed input such as an unsupported dimension or an unpublished metric name
  -> other side: treat zeros and nulls as a real answer meaning 'not observed', not as a tool failure; a `drilldown` or `financial_impact` for an unknown incident succeeds with an empty path and no money claimed
- 2026-08-29T20:32Z  andres  C2 `as_of` is the measurement watermark - latest observed event time less the lateness grace, floored to a bucket and clamped to the window asked about - and never wall-clock now, so the same events replayed in any order produce a byte-identical response
  -> other side: `as_of` can legitimately sit behind the end of the window you asked for, and it is stable across re-runs; do not read it as 'when the query ran' and do not use it as a freshness clock
- 2026-08-29T20:32Z  andres  incident identifiers are derived from onset and affected cohort rather than a counter or a clock, so a replay of the same events names the same incident, and detection persists each C3 record once with `lifecycle_state: detected` without ever overwriting a state a runner has already moved on
  -> other side: an incident id cited on screen or in a result stays valid across a replay; claim an incident through the guarded lifecycle update as agreed, and expect W2 never to reset your state
- 2026-08-29T20:49Z  derek  certifi MPL-2.0 question: OPEN and awaiting a human ruling
  -> other side: do not add further MPL or other non-permissive dependencies until settled; raise any new licence that is not MIT, Apache-2.0, BSD or ISC rather than assuming it is fine
- 2026-08-29T21:27Z  andres  ADR 0014 confirmed: W1's already-built uniform one-shape-per-topic contract stands as C1; no rebuild into three artificially different per-merchant native shapes. Merchants differ in data (volume, payment-method and country mix, provider routing, retry behaviour, values), not in serialisation format - that is what makes them meaningfully different to a detector, and W2's mapper registry (`detector/mappers.py`, already registering `clearwave.attempt.v1`) is the mechanism that absorbs a genuinely different native shape if one ever appears, so the capability is not lost by declining the exercise. This confirms only W2's half; derek's 19:04Z heterogeneity wording is his to release before raul marks the ADR Accepted.
  -> other side: raul rebuilds nothing in `worker/` on this account and may resume the two increments he named himself (backfill history, live judge trigger) - backfill is the one W2 concretely needs, since the contextual hour-of-week baseline can only replace today's trailing-window baseline once replayable history exists. Separately, W1's frozen `provider_timeout` decline code stays exactly as emitted; W2 is fixing the mapper translation to canonical `timeout` on its own side (raw provider code preserved), since normalising native decline codes is W2's job under `docs/ownership.md` - this was silently dropping every provider-degradation attempt from the counts, which matters because that scenario is the guaranteed demo path (`docs/demo-sequence.md`). derek: please confirm here whether the 19:04Z per-merchant-shape wording is released, so raul can move ADR 0014 to Accepted.
