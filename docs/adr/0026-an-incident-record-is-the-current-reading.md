# 0026 - An incident record is the current reading, not the first one

## Status

Accepted. Extends the one-cohort-one-record property of the 2026-08-30T03:59Z
watch decision; does not reverse any part of ADR 0025.

## Context

`store.save_incident` inserted with `INSERT OR IGNORE` and then updated with
`WHERE incident_id = ? AND lifecycle_state = 'watching'`. The guard was written
for the right reason: `detected` is the sole handoff signal to investigation
(DECISIONS.md, 2026-08-29T19:43Z) and a detection sweep must not clobber a state
another runner has moved on.

But it bound *every* field to that one condition, so the moment a row left
`watching` its numbers froze. Measured on a full rehearsal:

- The judge pressed Collapse at 07:00:46Z. The detector's own sweeps logged
  `severity: critical`, conversion 2.35%, USD 7,747/hour at risk.
- The board went on reading `CURRENT CONVERSION 52.4%` at 07:05 and the row
  stayed `high`. In a 39-minute run nothing was ever stored `critical`.
- A later verification run at 218f7d9 reproduced it exactly: hand
  `save_incident` the same record marked `critical` with score 0.95 on a
  `detected` row and it returns `False`, silently, with the row unchanged.

The same guard produced the second half of the problem. A rejected update does
not stop the next sweep, so the worsening outage was written as a *new* row -
including two rows on the byte-identical cohort key, `08b218aa` at onset
07:49:00Z and `59e2202b` at 07:52:00Z, both `country=CO|merchant_id=merchant-b|
provider=adyen`. One merchant-b/adyen outage reached eight `high` incidents on
adjacent cohort slices, and the queue read like a broken platform rather than
one localised fault.

Identity was the other half. `adopt_watch_identity` matched only cohorts where
one contained the other, and its live-state set omitted `claimed`, so a row that
investigation had claimed dropped out of the match and the next sweep hashed a
fresh id from a walked onset.

## Decision

An incident record describes something happening now. While the episode is
open, every later sweep of the same cohort **re-measures the row it already
has**: severity, severity score, the whole `detection` block, `change`,
`financial_impact`, `blast_radius`, `persistence`, `affected_cohort` and
`onset` are rewritten on the identifier the board is already showing. A fault
that deepens says so on that row; a fault that eases falls back down the same
way.

`lifecycle_state` is the one field this write does not own:

- a row still `watching` is upgraded to `detected` when the floors pass, and a
  watch write leaves it `watching`;
- a row that has left `watching` keeps exactly the state its owner set, however
  far its numbers move. Being re-measured is not being re-opened;
- a watch write can never pull a row out of a post-watch state;
- a `resolved` row is a closed episode and is never rewritten, so a fault that
  returns an hour later is a second incident, not a resurrection.

Identity follows from the same idea. Two readings are one episode when the
cohort dimensions they both name agree on at least one and agree on at least as
many as they conflict on (`detect.cohorts_same_episode`). Containment is the
no-conflict case, so every sharpening still matches; two sibling issuers under
one degraded provider collapse; a fault on another merchant *and* another
provider does not. An exact cohort-key match on a live row is always adopted,
so two open rows can never share one cohort key.

## Alternatives considered

**Leave the guard and let the board show the first reading.** This is what was
measured, and it is a board that lies as soon as the fault moves. The critical
beat of the pitch had never once happened.

**Let the detector move `lifecycle_state` too - reopen or resolve a row whose
numbers changed.** Rejected. `detected` is the handoff signal to C4 and
`investigating` is a claim another process holds; a detection sweep overwriting
either is the race the original guard existed to prevent. The fix is to narrow
what the write owns, not to widen it.

**A second identity scheme - a fault id above the incident id.** Rejected. Ids
are already replay-stable from onset plus cohort, and a second scheme is a
second thing to keep consistent. Adoption reuses the identity that exists.

**Group slices by a declared dimension hierarchy** (merchant and provider
identify a fault; issuer and country decorate it). Rejected: it encodes a
causal claim the data has not made, and the repository's whole localisation
design refuses to privilege a dimension in advance.

**Lower the `critical` threshold so the demo reaches it.** Rejected, and it was
not needed. Measured after this change, on eight hours of warm live-vocabulary
history: the row reaches `critical` on its own at score 0.796 against the
unchanged 0.70 threshold. Retuning the product to make a demo look good is the
thing the 2026-08-30T05:38Z decision already refused.

## Consequences

- One outage is one row, whose localisation and numbers move. Rehearsed offline
  on the demo path: warm history, developing stage, Collapse - a single record
  from the first watch through to `critical`, and `/api/incidents` serves
  `critical` for it.
- **Severity can now cross a band boundary in both directions on a live row.**
  Before this it was frozen at the first reading and could not oscillate.
  Anything that fires on a band - C5 escalation - must key on the transition it
  has already acted on, not on the value it reads, or a fault hovering at the
  boundary will re-page each time it crosses.
- A consumer must not treat any measured figure on an open incident as stable.
  `persistence.last_observed_at` says how recent the reading is.
- Re-measurement is something a *reading* does. When a fault clears entirely the
  detector reports nothing for that cohort, so the row keeps its last reading
  until its owner closes it. Closing a `detected` row is a lifecycle move and
  is deliberately not the detector's.
- The episode rule reads two cohorts and nothing else, so it cannot separate two
  unrelated faults that agree on one dimension and conflict on none - say
  `{country: CO, provider: adyen}` against `{country: CO, merchant_id: mx-1}`.
  That bound is pinned by a test rather than left to be discovered.
