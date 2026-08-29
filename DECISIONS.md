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
