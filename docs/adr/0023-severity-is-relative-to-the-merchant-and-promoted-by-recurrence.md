# 0023 - Severity is relative to the merchant, and recurrence may promote past the money ceiling

## Status

Accepted - supersedes ADR 0016.

## Context

ADR 0016 made money a ceiling and not only a term, and it was right to. Persistence and
trajectory climb on their own, so without a cap a trivially cheap incident that has been
grinding for an hour accumulates enough score to reach a middling band. That defect is real and
this record does not reintroduce it.

What 0016 got wrong is narrower and it is one assumption: the ceiling is an absolute
dollars-per-hour ladder, and the same three numbers apply to every merchant on the platform.
`detector/config.py:SEVERITY_LOSS_RATE_CEILING` is `((250.0, "low"), (2_000.0, "medium"),
(10_000.0, "high"))` - below $250/hour an incident can never exceed `low`, below $2,000 never
`medium`, below $10,000 never `high` - and `detector/detect.py:severity_of` applies that ladder
identically whoever the merchant is.

Yuno's product owners named the failure directly, and it is a shape problem rather than a
tuning problem. Merchants differ enormously in volume and ticket value. An airline has low
volume and a high ticket; a fast-food chain has high volume and a low ticket. A restaurant
chain losing sixty percent of its own traffic can sit under $2,000 an hour, so it is capped at
`medium` and never rings a phone. An airline losing six transactions clears $10,000 an hour and
goes `critical`. The second is not sixty times more urgent than the first. **Ranking by
absolute money alone systematically misses incidents on high-volume, low-ticket merchants**,
which is a whole class of Yuno's customers rather than an edge case.

There is evidence inside this repository that this is real rather than theoretical, and it is
the strongest single argument for the change. `worker/helpers/payment.py` carries this comment
above `CURRENCY_RANGES`:

> Per-currency minor-unit ranges so every merchant's tickets convert to
> roughly the same real-money band (USD ~8-120) via detector/config.FX_TO_USD.
> **Severity is bounded by money, therefore the generator must not produce
> pocket-change incidents for high-denomination currencies.**

When the money figures did not fit the rule, the *simulator* was adjusted so the rule would
behave. That is a legitimate thing to do to a simulator and an impossible thing to do to
production. A rule that requires its inputs to be pre-shaped into a single band is a rule that
only works on merchants who happen to already sit in that band.

Separately, the product owners asked a question the current rule cannot answer at all: "two
alerts of low priority in a short period - should that be medium? should that be high?" The
count of prior matching incidents is already measured and already published. `incident_history`
returns a `recurrence` block carrying `prior_matching_incidents`, its lookback and the matched
pattern (`detector/evidence.py`, specified in `docs/contracts/evidence-tools.md`). `severity_of`
never asks for it. A fault that keeps coming back is a different and worse thing than the same
fault happening once, and today the two are indistinguishable in the band.

## Decision

Severity remains business priority and nothing else. Two changes, and the ordering between them
is itself part of the decision.

**1. The ceiling becomes merchant-relative, taking whichever band is higher.**

Compute each merchant's own normal hourly value from the history the store already holds, and
express the incident's loss rate as a share of that normal. That share maps to a ceiling band
on its own ladder. The ceiling applied is **whichever is the higher band** of the share-based
one and the existing absolute-dollar one.

Taking the higher of the two is what makes this additive rather than a replacement. A
genuinely enormous absolute loss still ranks on a merchant whose normal is also enormous,
because the absolute ladder still applies. A proportionally catastrophic loss ranks on a small
merchant, because the share ladder applies. Neither ladder can pull a band *down* below what
the other allows.

When a merchant's normal is unknown - a new merchant, or too little history to be trusted - the
share ladder yields nothing and the absolute ladder alone applies. That fallback is exactly
today's behaviour, so the unknown-merchant path is unchanged rather than newly undefined.

**2. Recurrence promotes the band, and the promotion is allowed to exceed the ceiling.**

`severity_of` takes the count of prior matching incidents inside the published lookback. Two or
more promote the band one step; more promote further; the result is capped at `critical` and
nothing else.

**The promotion is applied after the ceiling and may exceed it.** This is a deliberate,
explicit relaxation of the guarantee 0016 made, and it is recorded here as a decision rather
than discovered later as a discrepancy. See Consequences.

**Ordering, which is the decision:**

1. Weighted score from the four business components, exactly as today.
2. Score maps to a band through `SEVERITY_THRESHOLDS`, exactly as today.
3. Ceiling applied - the higher of the merchant-relative band and the absolute-dollar band.
4. Recurrence promotion applied on top, allowed to exceed the ceiling, capped at `critical`.

Any other ordering gives a different answer. Promoting before the ceiling would let the ceiling
immediately undo the promotion, which makes recurrence a no-op on precisely the cheap incidents
it exists to surface.

**What does not change, and is preserved deliberately:**

- Severity is business priority only. Diagnostic confidence still belongs to investigation and
  the two still never collapse into one score.
- Statistical strength is still not an input. There is still no parameter through which
  evidence strength could reach `severity_of`. A huge z-score on trivial money stays low
  priority.
- The four band names - `low`, `medium`, `high`, `critical` - do not change, and neither does
  the shape of the C3 incident record. Consumers do not need to change.
- Money remains both a term and a ceiling. The log-scaled loss-rate term is untouched.
- Every new number is config under `CONFIG_VERSION`, like every number it joins.

The only assumption being withdrawn is that every merchant is the same size.

## Alternatives considered

- **Keep the absolute ladder and retune the three numbers** - rejected. There is no triple that
  is simultaneously right for an airline and a fast-food chain; that is the shape of the
  problem, not the calibration of it. Retuning moves which class of merchant is missed, it does
  not stop one being missed.
- **Replace the absolute ladder with the share ladder outright** - rejected. A merchant whose
  normal is enormous can lose a very large absolute sum at a small share of normal, and that
  incident is genuinely urgent to the platform. Taking the higher of the two keeps both truths.
- **Normalise the severity score itself by merchant rather than the ceiling** - rejected as a
  larger change than the evidence supports. The weighted score and its four components are not
  what is failing; the flat cap on top of them is. Changing only the ceiling is the minimal
  change that fixes the named defect, and it leaves the graded ranking behaviour that 0016 got
  by construction intact.
- **Let recurrence raise the score rather than the band** - rejected. A score contribution is
  swallowed by the ceiling and the effect disappears on cheap incidents, which are the entire
  motivating case. Promotion has to act on the band to act at all.
- **Recurrence promotion capped by the money ceiling** - rejected, and this is the sharpest
  call in the record. Under a cap, a $120/hour fault that has recurred five times in thirty
  days stays `low` forever, which is the exact behaviour the product owners asked about. The
  cap is what hides the pattern, so the cap is what has to give.
- **Folding diagnostic confidence into severity** - still rejected outright, as in 0016. It is
  the collapse the product baseline forbids.

## Consequences

**Incidents that are cheap in absolute dollars will now reach higher bands than they do today**,
by two independent routes: a large share of a small merchant's normal, and repetition. Some of
those bands carry escalation. Under the severity-to-channel binding in
`docs/contracts/notification-escalation.md`, `high` and `critical` reach Slack and phone, so
this change will ring phones that the current rule leaves silent. That is the intended effect
and it is also the risk: the cost of this change is paid in operator attention, and it should
be watched after it lands rather than assumed benign.

**Recurrence promotion deliberately relaxes 0016's guarantee that money bounds severity.** After
this record, an incident's band can exceed what its loss rate alone would permit. Stated
plainly, because a future reader will otherwise find a `critical` on an inexpensive incident and
read it as a bug. The reason is that a cheap fault which keeps returning is a real and
compounding problem - the third occurrence is evidence about the system, not about the money -
and the dollar cap is precisely the mechanism that hides it. Without permission to exceed the
ceiling, recurrence would change almost nothing, because the incidents that recur cheaply are
the ones the ceiling pins.

The guarantee 0016 protected is not abandoned wholesale. Persistence and trajectory still cannot
break the ceiling on their own; only a counted, published, independently verifiable recurrence
can. That is a narrower hole than the one 0016 closed, and it is opened knowingly.

`severity_of` gains two inputs - the merchant's normal hourly value and the prior matching
incident count - and both are already measured elsewhere in the plane, so neither introduces a
new source of truth. The recurrence input is the same number `incident_history` publishes, which
means an alert's band and the evidence bundle a judge reads cannot disagree about it.

The merchant-relative normal is computed from stored history, so it inherits the limits of that
history. On a short store the normal is thin and the fallback to dollars-only will fire often;
that is the correct behaviour and not a degradation, but it does mean the new ceiling has the
most effect exactly where the system has been running longest.

Consumers of C3 are unaffected structurally. The band vocabulary, the record shape and the
`lifecycle_state` vocabulary are untouched. What changes is which band a given incident carries,
which is a behavioural change W3 and W4 should expect rather than a contract change they must
absorb.
