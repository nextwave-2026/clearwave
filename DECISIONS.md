# Decisions

Append only. Never edit or delete an existing entry. Correct a past decision by appending a new decision. Keep the newest entries at the bottom.

Each entry is exactly two lines. The first carries an ISO 8601 UTC timestamp, **who made the decision** (your GitHub username, `derek` or `andres`), and what changed. The second states what the other side must now do differently - that line is the reason this file exists, so never leave it out.

**Commit this file straight to `main`** - no branch, no pull request. If your push is rejected because
the other side pushed first, run `git pull --no-rebase && git push`; union merge keeps both entries.

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
