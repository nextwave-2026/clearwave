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
    "affected_countries": 1,
    "affected_card_networks": 1,
    "affected_providers": 1
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
- `onset` (RFC 3339 UTC string) is the first observed time of the qualifying deviation.
- `persistence.is_persistent` (boolean), `observed_for_seconds` (non-negative integer), and
  `last_observed_at` describe duration and recency.
- `blast_radius` (object) reports the affected payment count and distinct dimension counts.
- `financial_impact` is the deterministic C2 financial-impact shape. `gmv_at_risk` is an estimate,
  not a platform-revenue claim.
- `severity` is one of `low`, `medium`, `high`, or `critical` and represents business priority.
- `lifecycle_state` is one of `detected`, `investigating`, `acknowledged`, `mitigated`, or
  `resolved`.

C3 deliberately has no `root_cause`, `hypothesis`, or `diagnostic_confidence` field. The detector
must not claim a cause, and diagnostic confidence belongs to C4.
