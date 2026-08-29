# C1b - Canonical ingestion event

C1b is the one normalised model every downstream component reads. W1 emits native
per-merchant event shapes and registers them; W2 normalises those into this model and persists
it. Nothing downstream of normalisation ever sees a native shape.

The reference implementation is `detector/schema.py`, which is the single definition. This
document explains the shape and the invariants; the module enforces them.

## Shape

```json
{
  "event_id": "att-00042-3",
  "payment_id": "pay-00042",
  "attempt_id": "att-00042-3",
  "attempt_number": 3,
  "occurred_at": "2026-08-30T04:12:07.109Z",
  "merchant_id": "merchant-a",
  "provider": "provider-p2",
  "payment_method": "card",
  "card_network": "mastercard",
  "country": "CO",
  "issuing_bank": "bank-x",
  "status": "declined",
  "normalized_decline_reason": "do_not_honor",
  "provider_raw_code": "05",
  "amount": 100.0,
  "currency": "USD",
  "latency_ms": 8410,
  "queue_depth": 318,
  "queue_delay_ms": 18000,
  "deployment_id": "deploy-2026-08-30.4",
  "service_id": "router-7c"
}
```

Required: `payment_id`, `attempt_id`, `attempt_number`, `occurred_at`, `merchant_id`, `provider`,
`payment_method`, `country`, `status`, `amount`, `currency`. A record missing any of them is
dead-lettered with its reason, never repaired by guessing, because a quietly wrong count cannot be
detected later.

`event_id` defaults to `attempt_id` when absent. Ingestion is idempotent on it, which is what makes
at-least-once delivery safe to consume.

## The dimensions

`merchant_id`, `provider`, `payment_method`, `card_network`, `country`, `issuing_bank`.

That set is the whole universe a cohort can be sliced on. Adding a dimension is a change to this
document and to `detector/schema.py`; no component adds one locally.

## Invariants

**Payment identity and attempt identity are both preserved.** One customer payment can produce many
provider attempts. Payment-level and attempt-level conversion are separate measurements and must
never be collapsed, and the gap between them is itself evidence: attempt conversion falling while
payment conversion holds means the fallback is absorbing a failure, and both falling together means
money is leaving.

**`normalized_decline_reason` comes from a closed vocabulary** so distributions are comparable
across providers. The current vocabulary is `DECLINE_REASONS` in `detector/schema.py`. A reason
outside it is rejected rather than passed through, because a free-text reason turns the decline
distribution into noise, and decline mix is one of the strongest discriminators between a provider
problem and an issuer problem. The provider's own code travels alongside in `provider_raw_code`,
preserved verbatim for evidence and never parsed for arithmetic.

**A failed status requires a reason, and a successful one forbids it.** `declined`, `error` and
`timeout` must carry a reason; `approved` and `pending` must not.

**`occurred_at` is event time**, RFC 3339 UTC. Every bucket, window and baseline in the detection
plane is computed from it and never from arrival or wall-clock time. That is what makes a replay of
a recorded stream produce byte-identical buckets, incidents and severity, which is how the detector
is tested at all and how the evaluator can rerun a scenario.

**Money is converted once, at ingestion**, using the frozen table in `detector/config.py`. A live
rate would make a replay produce a different number than the original run. An unknown currency is
an error rather than a silent pass-through.

## Native shapes and the mapper registry

W1 emits a native shape per merchant and registers it. A mapper is a pure function from one native
record to a canonical one, and `detector/mappers.py` holds the registry. Adding a source is adding a
mapper; nothing downstream changes.

Two mappers ship today because two shapes already exist:

| Registered name | Shape | Differences from C1b |
|---|---|---|
| `canonical` | already C1b | none; tolerates `attempt_ts`, `timestamp` and `event_time` as aliases for `occurred_at`, and `decline_reason` for `normalized_decline_reason` |
| `clearwave.attempt.v1` | the envelope circulated to W1 before normalisation moved to W2 | `attempt_ts` for event time, integer minor units in `amount_minor`, `decline_reason`, and a `timed_out` flag that becomes the `timeout` status |

A record's own `schema` field selects its mapper; a source that declares itself is never guessed at.
Otherwise the shape is inferred from the two fields that actually differ. An unregistered shape is
refused rather than coerced.

`tests/fixtures/native_attempt_v1.sample.json` is the envelope that was circulated, kept verbatim.
If it ever stops normalising, whoever built to it is broken and the test says so immediately.

Minor-unit conversion uses the ISO 4217 exponent for the currency, not a hardcoded two, because two
is wrong for JPY and CLP. An unregistered exponent is an error.

## What W1 needs to provide

1. Register each native merchant shape so W2 can write and version its mapper. If a simulator
   already emits the circulated `clearwave.attempt.v1` envelope, that shape is registered and works
   as-is; nothing needs redoing.
2. Key the raw payment topics so every attempt of one payment stays on one partition, in order.
   Without it, assembling retry chains becomes cross-partition coordination.
3. Emit an event timestamp on every record.
4. Provide replayable backfill history. A contextual baseline cannot be learned from the minutes
   before a judge fires an incident.
5. Never send anything derived from hidden ground truth. W2 has no read path to it and asks for
   none; if a field ever looks like it leaks the injected incident, refuse it and say so.
