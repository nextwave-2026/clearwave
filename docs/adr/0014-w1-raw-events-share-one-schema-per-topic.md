# 0014 - W1 raw events share one schema per topic, not one per merchant

## Status

Accepted - 2026-08-29 (derek released the per-merchant-shape wording)

## Context

derek's DECISIONS.md entry at 2026-08-29T19:04Z had committed W1 to per-merchant heterogeneous
raw event shapes, deliberately mirroring how a real orchestrator sees a different native
format from each merchant, with W2 normalising them into one canonical model (C1b). That
wording has since been released.

Separately, andres handed W1 a concrete C1 request (`README-FOR-RAUL.md`, delivered outside
git) asking for one uniform shape per event type shared across all three merchants: three
topics (`payments.attempts`, `payments.closed`, `ops.telemetry`), `payment_id`-keyed payment
topics, a closed and frozen `decline_reason` vocabulary, integer minor-unit amounts, an
`event_id` for dedup, and multi-attempt retry-chain fields (`attempt_number`, `is_retry`,
`previous_attempt_id`).

W1 built and verified that exact request end-to-end against real Kafka and Schema Registry:
three JSON-Schema-validated topics, full retry chains with provider reroute on each retry, and
`payments.closed` firing only once a chain actually stops (approved, exhausted, or abandoned) -
never per attempt.

Before that verification was reported, andres had already left a note (STATUS.md,
2026-08-29T19:38Z) saying his own C1 request is superseded by derek's 19:04Z model, since
normalisation is now W2's job - and that only three requirements survive from it: register
every native shape, key the raw topics by `payment_id`, and provide replayable backfill
history.

## Decision

W1 keeps the already-built uniform contract as its working implementation, now accepted as
C1. It already satisfies the three requirements andres says survive: schemas are registered
(one per topic, JSON Schema in the Schema Registry), the two payment topics are keyed by
`payment_id` so a payment's chain stays ordered on one partition, and backfill remains the
one open gap - tracked separately, not yet built.

W1 will not rebuild three artificially different native shapes; derek's heterogeneity wording
has been released.

## Alternatives considered

- Three genuinely different per-merchant native shapes, per derek's literal 19:04Z wording -
  not adopted. No concrete spec exists for what "merchant-b's native shape" should
  look like, so W1 would have to invent the differences itself with no product value: the
  heterogeneity is not visible anywhere in the judged demo. It would also mean andres writes
  three mapper functions instead of consuming one already-clean, already-working format - real
  W2 build time spent simulating realism rather than on the detection/investigation loop the
  challenge is actually graded on.

## Consequences

The uniform contract in `worker/registry/*.schema.json` stands as C1, and `INTERFACES.md`'s
C1 entry needs correcting - it still describes the abandoned per-merchant-topic-naming plan
from an earlier session, not what was actually built. Derek's heterogeneity wording is
released, so W1 does not rebuild against three distinct native shapes.
