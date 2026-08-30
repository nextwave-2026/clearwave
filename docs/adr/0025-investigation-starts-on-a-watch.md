# 0025 - Investigation starts on a watch

## Status

Accepted. Supersedes the claim-only-detected property of ADR 0024.

## Context

ADR 0024 and the 2026-08-30T03:59Z standing constraint said the investigation
daemon must claim only `detected` and never a watch. The reason was cost: a
live-stack review measured five watches on healthy traffic before anyone
touched the trigger, and claiming those would spend model calls on noise.

That constraint is now reversed. Investigating only after a deviation has
already become an incident means the system notices early and then waits for
the failure before doing the valuable work. The captain's ruling is that this
destroys the preventive value. An incident declaration is an escalation in
certainty and severity, not the event that starts the investigation.

The no-paging guarantee is not reversed. A watch still never escalates to Slack
or the phone. `surfaces/store.py` keeps `ESCALATABLE_STATES` as an allowlist of
post-detection states; a watch that now holds a C4 result is exactly the case
that guard was written for.

## Decision

The investigation daemon claims `lifecycle_state IN ('detected', 'watching')`
with the same atomic single-UPDATE guard that already stopped two runners
taking one row. A watch that later crosses the floors enriches that same
record. There is one cohort, one record, one story.

A watch investigation uses the existing C4 shape. It does not get a parallel
schema. Epistemically it is different: it gathers evidence, correlates, states
the business exposure it can measure, and offers plausible explanations and
preventive actions, while never asserting that something has failed and never
asserting a root cause the evidence does not support. Weak evidence is stated
immediately. The prompt is told it is looking at a developing deviation.

After a watch investigation the row returns to `watching`. It does not move to
`diagnosed`. That is what keeps C5 silent and what lets the detector still
upgrade the same row when the floors pass.

Refresh is bounded without weakening the ruling:

- One investigation per record per evidence fingerprint, not one per sweep.
- The fingerprint covers lifecycle, cohort, measured change, severity and watch
  reasons. Persistence duration, last-seen and onset walking are excluded, so a
  timer cannot retrigger a model call.
- A meaningful change - including the upgrade from `watching` to `detected` -
  spends one more call on the same identifier and versions the C4 result.
- The bound is observable: `investigation_bound.model_calls` is the store-wide
  count, and the runner prints `model_calls_this_process` after every persist.

A named `--incident-id` run is a manual re-run and may investigate even when
the fingerprint is unchanged. The daemon never does that.

## Alternatives considered

- Keep claiming only `detected` - rejected by the captain. It notices early and
  then waits for the failure.
- A parallel C4 schema for watches - rejected. The existing shape already
  carries competing explanations, missing evidence and qualitative diagnostic
  confidence independent of severity.
- Re-investigate every sweep - rejected. It is both expensive and noisy.
- Wait until leading-indicator false positives are fixed - rejected. That work
  is a separate lane. This path must not assume watches are rare.
- Leave a watch in `diagnosed` after investigation - rejected. `diagnosed` is
  on the paging allowlist, and the detector will not rewrite a row that has
  left `watching`.

## Consequences

Investigation becomes preventive. A TAM can read a C4 result on a watch while
there is still time to act, and the same record later carries the incident if
the floors are crossed.

Model cost rises from zero per watch to one per distinct evidence state. Five
start-up watches still cost five calls unless their fingerprints collide; they
do not cost a call every 45 seconds. The count is visible in the store.

C5 is unchanged. If a watch can page, this decision has been implemented
wrong. The surfaces allowlist and the restore-to-`watching` persist are both
load-bearing.

Clearwave still only recommends. Severity stays a detector figure. Diagnostic
confidence stays an investigation property. The investigation still never
computes a metric from raw events, and nothing here claims weekly or seasonal
learning.
