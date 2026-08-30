# C6 - Hidden truth and evaluator input

C6 is the record of what the simulator injected and what the event-time replay actually
produced. It is written by W1 and consumed only by the evaluator after a diagnosis exists. It is
not an input to C1b, C2, C3, or C4.

## Injected configuration

Each injected incident is recorded as one JSON object with this shape. The field names in
`affected_cohort` are the canonical C1b dimension names, not free-text labels.

```json
{
  "scenario_id": "provider-degradation",
  "scenario_name": "Provider degradation across cohorts",
  "affected_cohort": {
    "provider": "provider-p2"
  },
  "failure_mode": "provider_timeout_and_latency_degradation",
  "strength": {
    "metric": "timeout_rate",
    "baseline": 0.05,
    "target": 0.35,
    "unit": "ratio",
    "direction": "increase"
  },
  "start_time": "2026-08-29T10:00:00Z",
  "end_time": "2026-08-29T10:15:00Z",
  "event_time_bucket_seconds": 900
}
```

The configuration rules are:

- `scenario_id` is a stable identifier from [`docs/scenarios.md`](../scenarios.md). It is not a
  run id and must not be regenerated for a replay. `scenario_name` is human-readable catalogue
  text and is not used as a diagnostic hint.
- `affected_cohort` is an explicit dimension-to-value equality map. Its only allowed dimension
  names are `merchant_id`, `provider`, `payment_method`, `card_network`, `country`, and
  `issuing_bank`. It contains only dimensions actually constrained by this injection; an
  unconstrained dimension is omitted, never represented by `"all"`, `null`, or a wildcard.
- `failure_mode` names the behavior being simulated, such as provider timeouts, issuer
  over-declines, queue amplification, or a deployment regression. It describes the injection,
  not a cause supplied to the diagnostic path.
- `strength` states the measured signal, its baseline, target, unit, and direction. Ratios are
  in the range 0 to 1; counts, milliseconds, rates, and amounts use their stated unit. The
  simulator may include additional metric-specific fields, but it must always identify one
  primary metric and a numeric baseline and target when that metric is numeric.
- `start_time` is inclusive and `end_time` is exclusive. Both are RFC 3339 UTC timestamps and
  `start_time` precedes `end_time`.
- `start_time` and `end_time` must lie on the same event-time bucket boundaries used by W2.
  `event_time_bucket_seconds` records the bucket width used by the replay. Bucketing is by the
  event timestamp on each payment/attempt event, never by process arrival time, wall-clock time,
  or the time the evaluator is run. Replaying the same events and configuration therefore puts
  them in the same buckets and produces the same truth.

For a scenario bundle such as `simultaneous-incidents`, each independent injection is recorded
as its own C6 record using this same configuration. The records share `scenario_id` and have a
stable `instance_id` when more than one instance must be distinguished. A diagnosis is scored
against its instance's `affected_cohort`, not against the union of all instances.

## Hidden-truth record

After the replay window closes, W1 extends the configuration with the observed result:

```json
{
  "scenario_id": "provider-degradation",
  "scenario_name": "Provider degradation across cohorts",
  "injected": {
    "scenario_id": "provider-degradation",
    "scenario_name": "Provider degradation across cohorts",
    "affected_cohort": {
      "provider": "provider-p2"
    },
    "failure_mode": "provider_timeout_and_latency_degradation",
    "strength": {
      "metric": "timeout_rate",
      "baseline": 0.05,
      "target": 0.35,
      "unit": "ratio",
      "direction": "increase"
    },
    "start_time": "2026-08-29T10:00:00Z",
    "end_time": "2026-08-29T10:15:00Z",
    "event_time_bucket_seconds": 900
  },
  "observed": {
    "affected_cohorts": [
      {
        "relationship": "direct",
        "cohort": {
          "provider": "provider-p2"
        },
        "magnitude": {
          "metric": "payment_approval_conversion",
          "baseline": 0.92,
          "observed": 0.64,
          "absolute_delta": -0.28,
          "relative_change": -0.3043478261,
          "attempted_payments": 1000
        }
      },
      {
        "relationship": "side_effect",
        "cohort": {
          "provider": "provider-p2",
          "payment_method": "card"
        },
        "magnitude": {
          "metric": "timeout_rate",
          "baseline": 0.05,
          "observed": 0.35,
          "absolute_delta": 0.30,
          "attempted_payments": 1000
        }
      }
    ],
    "aggregate_magnitude": {
      "metric": "payment_approval_conversion",
      "baseline": 0.92,
      "observed": 0.64,
      "absolute_delta": -0.28,
      "attempted_payments": 1000,
      "gmv_at_risk": {
        "amount": 28000.0,
        "currency": "USD"
      }
    }
  },
  "evaluation": {
    "confounded": false,
    "priority_relations": []
  }
}
```

`injected` is the complete injected configuration. `observed.affected_cohorts` is a list of
explicit maps describing what telemetry actually showed. It must include the direct injected
slice when that slice produced an observable effect and may include any side-effect cohorts.
Each item is labelled `direct` or `side_effect` so the evaluator can distinguish the intended
slice from collateral impact. `observed.aggregate_magnitude` records the measured magnitude for
the incident window, including the metric, baseline, observed value, delta, volume, and any
business-impact values that were actually calculated. An incident with no qualifying effect may
have an empty `affected_cohorts` list and a zero or stable aggregate magnitude.

The evaluator uses `injected.affected_cohort` for cohort precision and recall. It does not replace
that target with a broad side-effect cohort: side effects remain useful truth for checking the
investigation narrative, but a diagnosis should not get full localisation credit for guessing a
collateral slice.

`evaluation` is evaluator-only metadata copied from the catalogue. `confounded: true` marks a
scenario where the observation window cannot distinguish the leading cause from a named
alternative. `priority_relations` contains relative business-priority checks, for example:

```json
{
  "priority_relations": [
    {
      "relation": "outranks",
      "scenario_id": "dramatic-low-volume-anomaly"
    }
  ]
}
```

This relation means that the high-impact scenario must rank above the named peer when both are
scored together. It does not prescribe an absolute severity number.

## Quarantine - non-negotiable

> **Hidden truth is written to a quarantined store that Detection and Investigation have NO read
> path to. Only the evaluator reads it, and only after a diagnosis exists.**

In particular:

1. W1 must not publish the hidden record, `scenario_id`, failure mode, strength, or truth-store
   location on the observable event stream.
2. W2 Detection must not import the hidden-truth module, query the hidden store, receive a C6
   object, or copy hidden fields into C1b, C2, or C3.
3. W3 Investigation must not import the evaluator or hidden-truth module, query the hidden store,
   receive C6 through a prompt/tool argument, or receive a scenario catalogue as diagnostic
   context. The C4 result is produced from C3 and C2 evidence only.
4. The evaluator runs in a separate, after-the-fact process. Its inputs are a completed diagnosis
   and a C6 record; its output cannot be sent back to Detection or Investigation.
5. The quarantine is a real process and storage boundary, not a naming convention or a promise
   that callers will ignore an available answer. A system that can see the answer has not
   demonstrated detection or investigation.

In a containerised run, each merchant worker bind-mounts its own store at
`state/ground_truth/<merchant_id>/` so the evaluator can read a closed record from the host.
That mount is attached only to the `worker-*` services. Detection and investigation still have
no import path, no volume, and no environment variable pointing at the store. The evaluator
refuses a record whose window is still open, and when more than one worker has a closed record
it requires `--instance-id` or `--scenario-id` rather than guessing which one to score.

No scenario identifier ever reaches detection or investigation. This is the rule in
[ADR 0012](../adr/0012-scenario-identifiers-never-reach-l4.md): the same diagnostic path serves
every scenario, the agent is never told which one is running, and neither layer may branch on a
scenario id. The evaluator may use the id only after the diagnosis has been produced so it can
select the corresponding hidden record and catalogue expectations.
