# 0024 - Leading indicators warn early, and nothing is predicted

## Status

Accepted

## Context

A payment provider does not fail instantly. It degrades in a sequence, and that sequence has a
consistent order:

1. Latency rises.
2. Timeouts start appearing in the decline mix.
3. Retries amplify, because a timed-out attempt is retried and the retry meets the same
   degraded provider.
4. Queues build.
5. **Conversion falls last.**

Detection today alerts on step five and only step five. Every incident this system reports is
therefore reported at the last moment the evidence permits, even though the earlier steps were
visible in the store the whole time.

They are visible because we already store them, on every run, with no new ingestion and no new
schema:

- `attempt.latency_ms` - normalised on every attempt (`detector/schema.py`).
- `attempt.normalized_decline_reason` - a closed vocabulary whose members include `timeout`,
  `issuer_unavailable`, `provider_error` and `rate_limited`.
- `attempt.attempt_number` - 1-based and validated, so retry amplification is a count we hold
  rather than one we would have to infer.
- `attempt.queue_depth` and `attempt.queue_delay_ms`, and the `telemetry_sample` table, which
  holds W1's per-service gauges (`detector/store.py`).

So the early signal is not missing. It is measured, stored, and unread.

The obvious way to use it is to forecast: fit something to the leading indicators and predict
when or whether conversion will fall. We are deliberately not doing that, and the reason is in
the Decision because the restraint *is* the decision.

## Decision

Add a second detection path, the early warning, which applies **the same comparison the detector
already performs** to leading-indicator columns instead of to conversion.

Concretely, it reuses the existing machinery rather than paralleling it:

- The same trailing-window baseline on the same cohort (`config.BASELINE_TRAILING_BUCKETS`).
- The same volume floor (`config.N_PAYMENTS_MIN`), so a thin cohort cannot produce a warning any
  more than it can produce an incident.
- The same shrinkage of small cohorts toward their parent (`config.SHRINKAGE_PRIOR_PAYMENTS`).
- The same contrast-based localisation and its sibling-separation rule
  (`config.LOCALISE_MIN_SEPARATION`), so a warning is localised the way an incident is.
- The same event-time bucketing (ADR 0018) and the same `CONFIG_VERSION` stamped on the output.

Only the measured quantity changes: median or high-percentile `latency_ms`, the share of
attempts carrying a timeout-family `normalized_decline_reason`, and the retry rate derived from
`attempt_number`, each compared against its own trailing baseline for that cohort.

**Nothing is trained, fitted, or forecast.** There is no model, no seasonality fit, no
extrapolation, and no predicted future number anywhere in this path. The output is a statement
about the present in the same form as every other statement this plane makes: *this cohort's
latency is materially worse than its own recent normal, by this much, measured this way.*

That restraint is the decision. A prediction we cannot defend is worse than no prediction. The
existing detection plane's whole claim is that every number it reports can be explained from
stored rows and versioned config, and a forecast breaks that claim at exactly the moment a judge
or an operator asks why. "Latency on this cohort is 4x its trailing hour" is defensible in one
sentence. "We expect conversion to fall in eleven minutes" is not defensible at all, and being
wrong once destroys trust in the whole surface.

**The early warning is carried as `lifecycle_state: watching` on the C3 record.**

This shape is not chosen here. `derek` recorded it in `DECISIONS.md` at 2026-08-30T03:59Z and
changed the C3 entry in `INTERFACES.md` to match: `watching` is a lifecycle state on the
existing incident record, not a separate side table, so one cohort keeps one record. This ADR
adopts that decision rather than restating it, and what follows is the set of properties that
decision depends on for the early warning to stay a weaker claim than an incident.

A forming signal is categorically different from a detected incident, and the separation is
enforced by behaviour rather than by storage:

- **`lifecycle_state: detected` remains the sole handoff signal to L4.** The investigation
  daemon claims only `detected`. `watching` must never be added to that claim SQL - doing so
  spends model calls on noise and turns a mild dip into a C4 that a TAM reads as an incident.
  Re-examining a watch is a detector loop, not a model loop.
- **C5 does not escalate a watch.** No Slack message, no phone call. A watch has no severity
  band claim to route on.
- **No existing field changes meaning**, and the record shape is otherwise untouched.
- When a watched cohort later crosses the real detection floors, the row is written or upgraded
  to `detected` and proceeds through the ordinary path, unchanged. One cohort, one record, one
  history.

Everything that made a separate record attractive is preserved by those four properties. What
the shared record buys in return is that a cohort's watch and its subsequent incident are the
same row, so the demo's central claim - we warned at this timestamp, before the cliff - is a
property of one record rather than a join between two.

## Alternatives considered

- **Train a model on the leading indicators to predict conversion collapse** - rejected. It is
  the obvious approach and the wrong one here. We have no replayable history to train on
  (`detector/config.py` states plainly that the trailing baseline is a placeholder until W1
  provides replayable backfill), no labelled incidents at any useful volume, and no way to
  explain a prediction to a judge in the terms the rest of this plane uses. The failure mode of a
  wrong forecast is worse than the failure mode of no forecast.
- **Lower the conversion detection floors so ordinary detection fires earlier** - rejected. The
  four floors of ADR 0015 each suppress a specific false positive; loosening them trades a whole
  class of correctness for a few minutes of warning. Reading a different, genuinely earlier
  signal is strictly better than reading the same late signal less carefully.
- **Emit early warnings as low-severity `detected` incidents** - rejected. It is the cheapest
  thing to build and it corrupts the incident record for both downstream layers: investigation
  would open runs on things that are not yet incidents, and escalation would route on a band
  that no longer means what it meant. Sharing the *table* is safe; sharing the *state* is not,
  and `detected` is the state that carries the strong claim.
- **A separate watch record or side table** - considered and not taken. It is the more
  conservative shape and it was this record's first instinct, because it makes the weaker claim
  structurally impossible to mistake for the strong one. `derek` decided otherwise at
  2026-08-30T03:59Z on the ground that one cohort should keep one record, and that is the better
  argument for the demo beat this exists to serve: the warning and the incident it preceded are
  the same row. The safety the side table would have given is recovered by the claim and
  escalation exclusions above, which are cheaper to state and are already enforced by the SQL
  that claims only `detected`.
- **Alert on raw thresholds, for example latency over 800ms** - rejected. An absolute threshold
  is wrong for the same reason ADR 0023 found the absolute money ladder wrong: cohorts have
  different normals, and one number cannot be right for all of them. Comparing each cohort
  against its own trailing baseline is the comparison this plane already knows how to defend.

## Consequences

The system gains warning ahead of conversion collapse without gaining anything it has to defend
as a prediction. The strongest thing it will ever say is that something is degrading now.

**More things will fire, and some of them will not become incidents.** A latency excursion that
recovers on its own is a true observation and a warning that led nowhere, and there is no
threshold that removes this without also removing the useful cases. That is acceptable only
because a watch is a visibly weaker claim than an incident: it is never claimed, never
escalated, and shown on a quieter rail. A watch surface with some noise in it is a different and
far cheaper mistake than an incident queue with some noise in it.
The volume floor, the shrinkage rule and the sibling-contrast rule are the guards, they are the
same guards detection already relies on, and they are the levers if the rate proves too high.

Investigation is unaffected: it claims `detected` and a watch is never `detected`. Escalation is
unaffected: it routes on a severity band a watch does not assert. W4's dashboard does change, by
design - it grows a quieter watch rail - and that is `juank`'s work under the same pivot, not a
consequence this record imposes on him unannounced.

The cost of sharing the record rather than separating it is that the claim and escalation
exclusions are now load-bearing conventions rather than structural impossibilities. If
`watching` is ever added to the daemon's claim SQL by someone being helpful, the system starts
paying for model calls on noise and a mild dip becomes a C4. That risk is named here because it
is the one thing about this shape that a future contributor could break without noticing.

The path adds read load on the same tables detection already scans, and it inherits the same
cost profile - which, as `docs/scaling.md` sets out, is dominated by cohort enumeration rather
than by the metric being measured. Any bound applied to the cohort search benefits both paths
equally.

This record describes a decision, not shipped behaviour. Nothing in it is built at the time of
writing.
