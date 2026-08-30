# C3 - Incident record

C3 is the deterministic detection-plane output consumed by investigation and surfaces. It describes
what changed and its business priority, not why it changed.

## Shape

```json
{
  "incident_id": "inc-2026-08-29-001",
  "affected_cohort": {
    "merchant_id": "merchant-a",
    "provider": "provider-p2",
    "payment_method": "card",
    "card_network": "mastercard",
    "country": "CO",
    "issuing_bank": "bank-x"
  },
  "change": {
    "metric": "payment_approval_conversion",
    "expected": 0.92,
    "actual": 0.64,
    "absolute_delta": -0.28,
    "relative_change": -0.3043478261,
    "unit": "ratio"
  },
  "onset": "2026-08-29T10:00:00Z",
  "persistence": {
    "is_persistent": true,
    "observed_for_seconds": 900,
    "last_observed_at": "2026-08-29T10:15:00Z"
  },
  "blast_radius": {
    "attempted_payments": 1000,
    "affected_merchants": 1,
    "affected_providers": 1,
    "affected_payment_methods": 1,
    "affected_card_networks": 1,
    "affected_countries": 1,
    "affected_issuing_banks": 1
  },
  "financial_impact": {
    "attempted_value": {
      "amount": 100000.0,
      "currency": "USD"
    },
    "expected_approval_rate": 0.92,
    "actual_approval_rate": 0.64,
    "estimated_lost_approved_volume": {
      "payments": 280,
      "amount": 28000.0,
      "currency": "USD"
    },
    "gmv_at_risk": {
      "amount": 28000.0,
      "currency": "USD"
    },
    "loss_per_hour": {
      "amount": 112000.0,
      "currency": "USD"
    }
  },
  "severity": "critical",
  "lifecycle_state": "investigating"
}
```

### Field definitions

- `incident_id` (string) uniquely identifies the incident.
- `affected_cohort` (object) contains equality filters over the registered C1 dimensions.
- `change` (object) contains the measured metric name, expected and actual values, absolute and
  relative deltas, and the metric unit. Conversion values are ratios in the range 0 to 1.
- `onset` (RFC 3339 UTC string) is the first observed time of the qualifying deviation. It is not
  bounded by the detection sweep window: the deviation is walked backwards, bucket by bucket, for as
  long as it stays qualifying, so an incident that began before the sweep reports when it actually
  began. The walk stops at the first bucket that is not degraded, which keeps onset the start of the
  current episode rather than of an earlier dip that has since recovered.
- `persistence.is_persistent` (boolean), `observed_for_seconds` (non-negative integer), and
  `last_observed_at` describe duration and recency.
- `blast_radius` (object) reports the affected payment count and one distinct count per registered
  C1 dimension: `affected_merchants`, `affected_providers`, `affected_payment_methods`,
  `affected_card_networks`, `affected_countries`, and `affected_issuing_banks`. The names are
  declared per dimension in `detector/metrics.py:BLAST_RADIUS_FIELDS` rather than generated from the
  dimension name, because generating them produced `affected_countrys` and `affected_merchant_ids`.
- `financial_impact` is the deterministic C2 financial-impact shape. `gmv_at_risk` is an estimate,
  not a platform-revenue claim.
- `severity` is one of `low`, `medium`, `high`, or `critical` and represents business priority.
- `lifecycle_state` is one of `watching`, `detected`, `investigating`, `acknowledged`,
  `mitigated`, or `resolved`.

#### `watching`

A watch is a developing deviation that has **not** crossed the detection floors. It is carried on
the same C3 record the cohort will keep if it becomes an incident, not on a separate store: one
cohort keeps one record, so the warning and the incident it becomes share an `incident_id`, and a
watch is updated in place as evidence accumulates. When the floors pass, the same row moves to
`detected`. The identifier is pinned at the first watch, so a later, sharper localisation or a
walked onset does not mint a second row. When a sweep no longer wants a watch - the deviation
recovered, or it was only warmup - the row moves to `resolved`. A watch is a claim about the
present; it does not sit on the board after that claim has stopped being true.

A watch is not an incident. Two behaviours still make that true rather than merely stated, and a
third has changed:

- **Investigation now starts on a watch.** The daemon claims `lifecycle_state IN ('detected',
  'watching')` so it can advise while there is still time to act. After a watch investigation the
  row returns to `watching`; it does not move to `diagnosed`. A later crossing of the floors
  enriches the same record. Re-investigation happens only when the evidence fingerprint changes,
  not on a timer. ADR 0025 records the reversal of the earlier claim-only-detected rule.
- **C5 does not escalate a watch.** `ensure_escalation` refuses any row whose `lifecycle_state` is
  not in `ESCALATABLE_STATES` (`detected`, `investigating`, `diagnosed`, `acknowledged`,
  `mitigated`, `resolved`) before it reads severity, claims a channel, or fires. That lifecycle
  allowlist is the no-paging mechanism. `watching` is not on it, including when the row already
  holds a C4 result.
- **`severity` on a watch is always `low`.** That is defence in depth, not the gate. C5's channel
  map would still not page a `low` row, but a caller that pointed escalation at a watch while
  copying a high severity would still be stopped by the lifecycle allowlist.
- **`financial_impact.projected_loss_per_hour`** is what an hour at the currently measured
  shortfall would cost, applied to the cohort's typical hourly attempted value from the trailing
  baseline. It is labelled projected because it is not realised money, it is a separate key from
  `loss_per_hour`, and nothing that ranks severity reads it.

`detection.watch` carries why the cohort is watched and not yet reported: the reasons, the watch
floor vector, the detection floors not yet met, the trajectory, and the leading indicators with
their baselines. Nothing in it is trained, fitted or forecast, and it never states a future
number - the honest sentence is that this cohort is unusual for itself against its last hour and
is getting worse.

C3 deliberately has no `root_cause`, `hypothesis`, or `diagnostic_confidence` field. The detector
must not claim a cause, and diagnostic confidence belongs to C4.
