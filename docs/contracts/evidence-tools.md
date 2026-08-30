# C2 - Evidence-query tools

C2 is the read surface used by the investigation agent. Each tool is a standalone Python 3
subprocess: it reads one JSON object from stdin, writes one JSON object to stdout, and writes no
human-oriented output. The entry points are in `stubs/evidence/`.

C2 is an interface contract, not an implementation roster. Eleven of the twelve tools measure the
canonical events W2 has stored and are implemented by W2. `external_status` corroborates from a
third-party source, so W3 implements it and it remains fixture-backed here; implementation
ownership follows the data source. Nothing on the wire distinguishes them.

Eleven of the twelve measure the payments. `ingest_health` measures the measuring - what reached the
store, what was refused, and how recent it is - and is the only tool whose subject is the pipeline.

## Common protocol

Invoke a tool as follows:

```sh
printf '%s\n' '{"cohort":{"merchant_id":"merchant-a"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}' \
  | python3 stubs/evidence/cohort_metrics.py
```

A successful response always contains:

- `query_id` - stable identifier for the canonical JSON value of the exact `{tool, input}` call.
  The reference stubs use `q_<tool>_<first-16-sha256-hex>`.
- `as_of` - RFC 3339 UTC timestamp at which the returned evidence was measured.

The remaining top-level fields are tool-specific. A failure exits non-zero and prints only this
shape as JSON:

```json
{"error":{"code":"invalid_input","message":"stdin must contain a JSON object"}}
```

`cohort` is an object of equality filters. Supported dimensions are `merchant_id`, `provider`,
`payment_method`, `card_network`, `country`, and `issuing_bank`; implementations may add a
registered C1 dimension without changing the meaning of existing fields. `window` is an inclusive
start/exclusive end UTC interval with RFC 3339 timestamps.

## 1. `cohort_metrics`

**Purpose:** Return the measured conversion and volume for one cohort. Payment-level conversion
counts distinct customer payments; attempt-level conversion counts provider attempts. These two
levels must remain explicit and must never be collapsed.

**Input:**

- `cohort` (object, required) - dimension equality filters.
- `window` (object, required) - `start` and `end` timestamps.

**Output fields:** `cohort`, `window`, `payment_metrics` (`attempted_payments`,
`approved_payments`, `approval_conversion`, and optional expected/baseline fields),
`attempt_metrics` (`attempts`, `approved_attempts`, `approval_conversion`, `failed_attempts`),
`volume.attempted` and `volume.approved` (each `{amount,currency}`), `decline_mix` (reason/count/share),
and `baseline`.

**Example call:**

```json
{"cohort":{"merchant_id":"merchant-a","provider":"provider-p2","country":"CO","card_network":"mastercard"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}
```

**Example response:**

```json
{"query_id":"q_cohort_metrics_4c7bf85539781845","as_of":"2026-08-29T10:15:00Z","payment_metrics":{"attempted_payments":1000,"approved_payments":640,"approval_conversion":0.64},"attempt_metrics":{"attempts":1350,"approved_attempts":640,"approval_conversion":0.4740740741},"volume":{"attempted":{"amount":100000.0,"currency":"USD"},"approved":{"amount":64000.0,"currency":"USD"}},"decline_mix":[{"reason":"timeout","count":505,"share":0.7112676056}]}
```

## 2. `cohort_compare`

**Purpose:** Return the same core payment-level, attempt-level, and volume metrics for the target
cohort, its sibling cohorts, and its parent cohort. This shows whether a deviation is isolated or
inherited.

**Input:**

- `cohort` (object, required) - target filters.
- `window` (object, required).
- `compare_dimensions` (array of strings, optional) - dimensions that define sibling slices.

**Output fields:** `target`, `siblings` (array), and `parent`. Each contains `cohort`,
`payment_metrics`, `attempt_metrics`, and `volume` with the same field meanings as
`cohort_metrics`.

**Example call:**

```json
{"cohort":{"merchant_id":"merchant-a","provider":"provider-p2","country":"CO"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"},"compare_dimensions":["provider","country"]}
```

**Example response:**

```json
{"query_id":"q_cohort_compare_b55163acfb4eb830","as_of":"2026-08-29T10:15:00Z","target":{"payment_metrics":{"attempted_payments":1000,"approved_payments":640,"approval_conversion":0.64},"attempt_metrics":{"attempts":1350,"approved_attempts":640,"approval_conversion":0.4740740741}},"siblings":[{"label":"same merchant, provider P3 sibling","payment_metrics":{"attempted_payments":600,"approved_payments":558,"approval_conversion":0.93}}],"parent":{"label":"merchant A across all dimensions","payment_metrics":{"attempted_payments":5000,"approved_payments":4300,"approval_conversion":0.86}}}
```

## 3. `drilldown`

**Purpose:** Return the localisation path followed for an incident, level by level, including the
metric observed at each level and the deterministic reason the path stopped.

**Input:**

- `incident_id` (string, required).
- `window` (object, optional) - defaults to the incident's observation window.
- `levels` (array of strings, optional) - requested dimension order.

**Output fields:** `incident_id`, `levels` (each `{level, cohort, metrics, reason}`), `stopped_at`,
and `stop_reason`.

**Example call:**

```json
{"incident_id":"inc-2026-08-29-001","levels":["merchant","provider","country","card_network","issuing_bank"]}
```

**Example response:**

```json
{"query_id":"q_drilldown_dc1bad2929028414","as_of":"2026-08-29T10:15:00Z","incident_id":"inc-2026-08-29-001","levels":[{"level":"provider","cohort":{"provider":"provider-p2"},"metrics":{"payment_approval_conversion":0.64},"reason":"Provider P2 isolates the shift."}],"stopped_at":"provider_vs_issuing_bank","stop_reason":"No Provider P2 traffic from another issuer and no Bank X traffic through another provider."}
```

## 4. `decline_breakdown`

**Purpose:** Return the normalised decline-reason distribution for a cohort and its shift against a
baseline. Shares use the reported failed-attempt denominator, which is explicit in the response.

**Input:**

- `cohort` (object, required).
- `window` (object, required).
- `baseline_window` (object, optional).

**Output fields:** `cohort`, `window`, `normalised_denominator`, `reasons` (each
`{reason,count,share,baseline_share,shift}`), and `baseline`.

**Example call:**

```json
{"cohort":{"merchant_id":"merchant-a","provider":"provider-p2","country":"CO"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}
```

**Example response:**

```json
{"query_id":"q_decline_breakdown_fd55d9c698a6e062","as_of":"2026-08-29T10:15:00Z","normalised_denominator":"failed_attempts","reasons":[{"reason":"timeout","count":505,"share":0.7112676056,"baseline_share":0.1,"shift":0.6112676056},{"reason":"issuer_decline","count":165,"share":0.2323943662,"baseline_share":0.6,"shift":-0.3676056338}]}
```

## 5. `retry_stats`

**Purpose:** Describe retry depth and amplification without treating retries as new customer
payments. Queue depth and delay provide operational context for a retry storm.

**Input:**

- `cohort` (object, required).
- `window` (object, required).

**Output fields:** `payments`, `attempts`, `retried_payments`, `retry_depth` (maximum and
count distribution), `attempts_per_payment`, `retry_amplification_factor`, and `queue`
(start/end/peak depth and delay percentiles).

**Example call:**

```json
{"cohort":{"merchant_id":"merchant-a","provider":"provider-p2"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}
```

**Example response:**

```json
{"query_id":"q_retry_stats_4be170e12f0e2d8f","as_of":"2026-08-29T10:15:00Z","payments":1000,"attempts":1350,"retried_payments":300,"retry_depth":{"max":2,"distribution":{"0":700,"1":250,"2":50}},"attempts_per_payment":1.35,"retry_amplification_factor":1.35,"queue":{"depth_start":42,"depth_end":318,"depth_peak":352,"delay_p95_ms":18000}}
```

## 6. `operational_metrics`

**Purpose:** Return latency percentiles, error and timeout rates, service/runtime health, and the
deployment identity associated with a cohort or service.

**Input:**

- `target` (object, required) - `{kind:"cohort"|"service", ...filters}`.
- `window` (object, required).

**Output fields:** `target`, `window`, `latency_ms` (`p50`, `p95`, `p99`), `error_rate`,
`timeout_rate`, `service_health`, `runtime_health`, and `deployment`.

**Example call:**

```json
{"target":{"kind":"cohort","merchant_id":"merchant-a","provider":"provider-p2"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}
```

**Example response:**

```json
{"query_id":"q_operational_metrics_e7fbb154c1351d42","as_of":"2026-08-29T10:15:00Z","latency_ms":{"p50":420,"p95":1800,"p99":4200},"error_rate":0.018,"timeout_rate":0.35,"service_health":{"status":"degraded"},"runtime_health":{"status":"healthy"},"deployment":{"service":"payment-router","deployment_id":"deploy-2026-08-29.3"}}
```

## 7. `confounding_check`

**Purpose:** Deterministically establish whether two dimensions are structurally inseparable in the
observed window. This is a data property, not an LLM judgement. The cross-tabulation and criterion
make the conclusion auditable.

**Input:**

- `dimension_a` (string, required).
- `dimension_b` (string, required).
- `window` (object, required).
- `cohort` (object, optional) - restrict the observation first.

**Output fields:** `dimension_a`, `dimension_b`, `window`, `structurally_inseparable` (boolean),
`criterion`, `cross_tabulation` (`dimensions` and `rows`), `observed_mappings`, and `interpretation`.
A false result is valid and must still include the cross-tabulation.

**Example call:**

```json
{"dimension_a":"provider","dimension_b":"issuing_bank","window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"},"cohort":{"merchant_id":"merchant-a"}}
```

**Example response:**

```json
{"query_id":"q_confounding_check_8efbcfeb03db80ca","as_of":"2026-08-29T10:15:00Z","dimension_a":"provider","dimension_b":"issuing_bank","structurally_inseparable":true,"cross_tabulation":{"dimensions":["provider","issuing_bank"],"rows":[{"provider":"provider-p2","issuing_bank":"bank-x","payments":1000,"attempts":1350},{"provider":"provider-p3","issuing_bank":"bank-y","payments":600,"attempts":620}]},"interpretation":"The data cannot discriminate a provider P2 cause from a Bank X cause."}
```

## 8. `incident_history`

**Purpose:** Return prior incidents for a merchant, a filtered cohort, or the whole store, so
recurrence is visible and a stored `incident_id` can be discovered.

**Input:**

- `merchant_id` (string, optional) - omit it, or send `null`, to answer across every merchant.
  A present but empty or non-string value is still refused as `invalid_input`: a caller that meant
  to name a merchant and got it wrong must hear so rather than silently receive a store-wide answer.
- `cohort` (object, optional) - additional filters.
- `window` (object, optional) - lookback interval.

**Output fields:** `merchant_id` (echoed, `null` when omitted), `cohort_filter`, `incidents` (each
prior incident summary, carrying `incident_id`, `onset`, `lifecycle_state`, `severity` and `cohort`),
and `recurrence` (matching count, lookback and pattern).

Omitting `merchant_id` is the only route in this surface from "no cohort" to a stored `incident_id`,
and therefore the only way a question scoped to all traffic can reach `drilldown` (section 9) or
`financial_impact` (section 10), both of which require one. Naming a merchant behaves exactly as it
did before this was added.

**Example calls:**

```json
{"merchant_id":"merchant-a","cohort":{"provider":"provider-p2","country":"CO"}}
```

```json
{}
```

**Example response:**

```json
{"query_id":"q_incident_history_240ddddcbce2998b","as_of":"2026-08-29T10:15:00Z","merchant_id":"merchant-a","incidents":[{"incident_id":"inc-2026-08-10-004","severity":"high","payment_approval_conversion":{"expected":0.91,"actual":0.78}}],"recurrence":{"prior_matching_incidents":2,"lookback_days":30,"pattern":"provider-p2 and country CO"}}
```

## 9. `external_status`

**Purpose:** Return third-party provider health as optional corroboration. An unavailable source is
a successful response, not a tool failure, and must never stop diagnosis.

**Input:**

- `provider` (string, required).
- `window` (object, optional).
- `source` (string, optional) - requested status source.

**Output fields:** `provider`, `status` (including `operational`, `degraded`, `outage`, and
`unavailable`), `source`, `checked_at`, `reason` when unavailable, and `diagnostic_effect`.

**Example call:**

```json
{"provider":"provider-p2","source":"provider-status-adapter"}
```

**Example response:**

```json
{"query_id":"q_external_status_25bd2e0ba013d3a3","as_of":"2026-08-29T10:15:00Z","provider":"provider-p2","status":"unavailable","source":"provider-status-adapter","checked_at":"2026-08-29T10:15:00Z","reason":"The provider status endpoint did not answer within the adapter timeout."}
```

## 10. `financial_impact`

**Purpose:** Return deterministic business impact for an incident. Values are GMV-at-risk
estimates, not platform-revenue claims.

**Input:**

- `incident_id` (string, required).
- `window` (object, optional) - defaults to the incident's exact persisted detection window,
  the half-open interval used to compute its C3 `financial_impact`. It does not default to the
  incident's onset-to-last-observed episode interval; callers must provide that interval
  explicitly when they want episode totals.

**Output fields:** `incident_id`, `window`, `attempted_value`, `expected_approval_rate`,
`actual_approval_rate`, `estimated_lost_approved_volume` (payments and amount), `gmv_at_risk`,
`loss_per_hour`, and labelled `assumptions`.

**Example call:**

```json
{"incident_id":"inc-2026-08-29-001"}
```

**Example response:**

```json
{"query_id":"q_financial_impact_f73da703f566fa22","as_of":"2026-08-29T10:15:00Z","incident_id":"inc-2026-08-29-001","attempted_value":{"amount":100000.0,"currency":"USD"},"expected_approval_rate":0.92,"actual_approval_rate":0.64,"estimated_lost_approved_volume":{"payments":280,"amount":28000.0,"currency":"USD"},"gmv_at_risk":{"amount":28000.0,"currency":"USD"},"loss_per_hour":{"amount":112000.0,"currency":"USD"}}
```

## 11. `metric_series`

**Purpose:** Return one named metric for one cohort over ordered event-time buckets. This is the
only tool that answers "since when": incident onset, a severity trajectory and any statement about
a trend read it rather than deriving a series from repeated point queries.

It is deliberately a separate tool rather than a mode of `cohort_metrics`. Folding a series into
`cohort_metrics` would change the response shape of a tool two workstreams already build against,
and rule 4 of `docs/ownership.md` keeps contract changes additive during the build window.

**Input:**

- `cohort` (object, optional) - dimension equality filters; omitted or `{}` means all traffic.
- `window` (object, required) - `start` and `end` timestamps.
- `metric` (string, optional) - defaults to `payment_approval_conversion`. The published set is
  `payment_approval_conversion`, `attempt_approval_conversion`, `attempted_payments`,
  `approved_payments`, `attempts`, `failed_attempts`, `attempted_value_usd`, and
  `retry_amplification_factor`. Any other name is refused by the error envelope with the supported
  set in its message, never silently replaced by a default.
- `bucket_seconds` (positive integer, optional) - defaults to the detector's bucket width, 60.

**Output fields:** `cohort`, `window`, `metric`, `bucket_seconds`, `watermark`, `measured_through`,
and `points`. Each point is `{bucket_start, bucket_end, value, samples}`, oldest first.
`value` is `null` where the metric is undefined for that bucket, for instance a conversion with no
payments in it. `samples` is the denominator the value was computed over, so a point that moved on
three payments is not read as a collapse.

Buckets are cut on event time and only fully closed buckets behind the lateness watermark are
returned: `watermark` is the point measurement is complete to, and `measured_through` is the end of
the last bucket reported. A trailing partial bucket is omitted rather than reported low, because a
minute that is not over yet always looks like a drop. A payment falls in the bucket of its first
attempt and an attempt falls in the bucket of its own event time, so a retry never moves a payment
forward in time.

**Example call:**

```json
{"cohort":{"provider":"provider-p2","country":"CO"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"},"metric":"payment_approval_conversion"}
```

**Example response:**

```json
{"query_id":"q_metric_series_eb6e1d7e3022d329","as_of":"2026-08-29T10:14:00Z","cohort":{"provider":"provider-p2","country":"CO"},"metric":"payment_approval_conversion","bucket_seconds":60,"watermark":"2026-08-29T10:14:00Z","measured_through":"2026-08-29T10:14:00Z","points":[{"bucket_start":"2026-08-29T10:00:00Z","bucket_end":"2026-08-29T10:01:00Z","value":0.92,"samples":50},{"bucket_start":"2026-08-29T10:01:00Z","bucket_end":"2026-08-29T10:02:00Z","value":0.64,"samples":48}]}
```

## 12. `ingest_health`

**Purpose:** Answer "is anything actually arriving?" from the store alone. Every other tool measures
the traffic; this one measures the pipeline that carried it - how many records survived normalisation
into the store, how many were refused and why, how recent the newest observed event is, and how far
that newest event sits ahead of the point measurement is complete to.

It takes no `cohort` and no `window`, and refuses either through the error envelope. A freshness
figure narrowed to a window is not a freshness figure, and a filter this tool does not honour would
be a caller quietly reading a wider answer than the one asked for.

**Input:** none. The request object must be empty; any key is refused with `invalid_input`.

**Output fields:** `watermark`, `accepted`, `stored` (`attempts`, `telemetry_samples`,
`payments_closed`), `rejected`, `dead_letter` (`count`, `distinct_reasons`, `reasons`, `by_source`),
`oldest_event_at`, `newest_event_at`, `newest_by_kind`, `lag_seconds`, `lateness_grace_seconds`, and
`not_measured`.

- `accepted` is normalised payment attempts the store holds, after de-duplication. It is a row count,
  not a running total of what a consumer saw.
- `rejected` and `dead_letter.count` are **one measurement published twice, equal by construction**.
  A refused record is dead-lettered in the same statement that rejects it. Both names exist because
  "was anything rejected" and "what is in the dead-letter queue" are the same question of this store
  and a caller should not have to know that.
- `dead_letter.reasons` is grouped by reason, ordered by count then reason, and capped at ten entries;
  `distinct_reasons` is always the full count, so a truncated list is visible rather than misleading.
  `by_source` splits the same rows by the ingest path that refused them.
- `lag_seconds` is `newest_event_at` minus `watermark`, **event time against event time**. It says how
  much of what has arrived is not yet measured, and it is unchanged on a replay. It is not "how long
  since a record arrived" and must never be presented as a wall-clock freshness figure.
  `lateness_grace_seconds` is the configured grace the watermark subtracts, published so the number is
  readable without knowing the detector's configuration.
- `not_measured` names a counter this tool deliberately does not report, with the reason. It is a
  statement about the tool, not a counter.
- `oldest_event_at`, `newest_event_at`, `watermark`, `as_of` and `lag_seconds` all describe the
  **canonical attempt stream**, which is what `as_of` has meant on every tool in this contract since
  the first one. Telemetry and closed-payment rows are stored beside attempts and counted in `stored`,
  but they do not move the watermark, and this tool does not redefine it. So that a store holding only
  telemetry cannot report "nothing observed" while plainly holding something, `newest_by_kind` gives
  each record kind its own newest event time - `attempts`, `telemetry_samples`, `payments_closed`,
  each an RFC 3339 timestamp or `null`. It is a second set of readings, never a replacement for the
  first: `newest_by_kind.attempts` and `newest_event_at` are the same value.

**`duplicates` is absent on purpose.** At-least-once delivery is turned into exactly-once counting by
`INSERT OR IGNORE` on `event_id`, so a redelivered record leaves no row behind. The count exists only
in the consumer's in-memory progress for the length of one run and is printed on its stdout. There is
no honest way to recover it from the store, so it is named in `not_measured` rather than estimated.

**An empty store answers honestly**, as everywhere else: zero counters, an empty reason list, and
`null` for `oldest_event_at`, `newest_event_at` and `lag_seconds` - which are undefined rather than
zero when nothing has been observed.

**Example call:**

```json
{}
```

**Example response:**

```json
{"query_id":"q_ingest_health_3f2a1c9d5e7b4086","as_of":"2026-08-30T05:18:00Z","watermark":"2026-08-30T05:18:00Z","accepted":1836,"stored":{"attempts":1836,"telemetry_samples":240,"payments_closed":0},"rejected":0,"dead_letter":{"count":0,"distinct_reasons":0,"reasons":[],"by_source":[]},"oldest_event_at":"2026-08-30T04:00:00Z","newest_event_at":"2026-08-30T05:19:00Z","newest_by_kind":{"attempts":"2026-08-30T05:19:00Z","telemetry_samples":"2026-08-30T05:18:30Z","payments_closed":null},"lag_seconds":60,"lateness_grace_seconds":30,"not_measured":{"duplicates":"redelivered records are dropped by INSERT OR IGNORE on event_id and leave no row behind; the count lives only in the consumer run that saw them, so the store cannot report it"}}
```

## Measurement notes

These hold for every measured tool. They are implementation behaviour a caller can rely on, not new
fields.

**Where the data comes from.** Each tool reads one SQLite store, located by the `CLEARWAVE_DB`
environment variable and defaulting to `state/clearwave.db` relative to the working directory. A
caller that sets it once points the whole system - detector CLI and every tool - at one file. The
store is created empty if it does not exist, so a tool never fails merely because nothing has been
ingested yet. `python3 -m detector seed` fills a store with the repository's own deterministic
synthetic events for a demo or a manual call.

**`as_of` is the measurement watermark, not the wall clock.** It is the latest observed event time,
less the lateness grace, floored to a bucket, and clamped to the end of the window asked about. Two
runs over the same events therefore return the same `as_of`, which is what makes a cited response
reproducible; a store that has observed nothing reports the epoch.

**An empty store answers honestly.** Counters are zero, an undefined rate is `null`, a list is
empty, and a tool asked about an incident that is not stored says so in `stop_reason` or in
`assumptions` and claims no money. No response ever falls back to a fixture number. Malformed input
is still a refusal through the error envelope, with `invalid_input` for an unsupported dimension,
an unpublished metric name, or a missing required field.

**Cohort vocabulary.** A `cohort` is validated against the six published dimensions and anything
else is refused. `operational_metrics` additionally accepts `service` on a `target` of
`kind: "service"`, because a service is not a cohort.

**Derived and unobserved values.** `operational_metrics.service_health` is derived from first-party
attempts - degraded once the combined error and timeout rate reaches the configured threshold - and
carries the criterion that produced it. `runtime_health` is measured from W1's `ops.telemetry`
samples for the services observed on the target: it reports `degraded` when any sample in the window
declares itself unhealthy, `healthy` otherwise, with the gauges and the criterion behind the verdict.
Where no sample has been observed it reports `unobserved` with its reason, which is what the
file-based path returns, because the canonical attempt event carries no runtime signal and W2 does
not infer one from attempts. The two never merge: `service_health` stays derived from attempts and
`runtime_health` stays reported by the service.

**Comparison shapes.** In `cohort_compare`, a sibling replaces exactly one dimension of the target
with another observed value of that dimension, and a sibling with no traffic in the window is
omitted rather than reported as zero. The parent is the target's merchant across all its other
dimensions, or all traffic when the target is already merchant-wide or platform-wide.

**Recurrence matching.** In `incident_history`, an incident whose affected cohort names no merchant
is platform-wide and therefore matches every merchant asked about; one that names a different
merchant never matches. Any `cohort` filter supplied alongside `merchant_id` must match the stored
cohort exactly.

**Baselines.** Where a tool reports a baseline without being given one, it is the detector's own
trailing window on the same cohort, and the window it used is stated in the response.

## Caller rules

- The investigation agent may not compute a metric itself. If it needs a statistic this surface does
  not expose, it requests that W2 add it here instead of deriving it from events or other results.
- Every factual claim in an investigation result cites the `query_id` that produced it. Evidence
  items use the exact query identifier, not a copied metric or an uncited narrative assertion.
- External status is corroboration only. First-party observations remain usable when the external
  source is unavailable or disagrees.
