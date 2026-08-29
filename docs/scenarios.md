# ClearWave scenario catalogue

This catalogue is the concrete contract for deterministic injection, detection, investigation, and
after-the-fact evaluation. IDs are stable and are safe to use in the simulator and evaluator only.
They are never sent to Detection or Investigation. Every scenario uses the same observable event
path; none is a diagnostic branch.

The three **guaranteed demo scenarios** are:

1. `provider-degradation`
2. `provider-issuer-confounded`
3. `high-impact-small-percentage`

The other scenarios are documented and evaluator-eligible. They are not guaranteed to be built or
rehearsed for the live demo.

## Common scenario rules

- An injection's affected cohort is an explicit map containing only constrained C1b dimensions:
  `merchant_id`, `provider`, `payment_method`, `card_network`, `country`, and `issuing_bank`.
- `start_time` is inclusive, `end_time` is exclusive, and both align with W2's event-time bucket
  boundaries. The simulator changes observable behavior based on each event's event timestamp,
  not arrival time.
- Strength describes an injected observable change. It is not a cause supplied to the diagnostic
  path. The detector must establish the change from telemetry and the investigator must reason
  from C2 evidence.
- A detector can surface a cohort more broadly or more narrowly while investigating, but its
  final localisation should contain the dimension-value pairs supported by evidence. The
  evaluator reports pair precision and recall against the direct injected slice; it does not
  award full localisation credit for a side effect.
- "Wrong answer" is an evaluator requirement, not merely a presentation note. A plausible-sounding
  but unsupported cause is wrong even when it is the injected cause.

## 1. `provider-degradation` - Provider degradation across cohorts

**Demo status: GUARANTEED.**

- **What is injected:** Provider P2 develops elevated timeouts and latency for the injection
  window. A representative strength is timeout rate `0.05 -> 0.35` and payment approval
  conversion `0.92 -> 0.64` across otherwise normal traffic.
- **Affected cohort dimensions:** `{ "provider": "provider-p2" }`. The slice spans multiple
  merchants, methods, countries, and issuers rather than one accidental leaf cohort.
- **What the detector should find:** A persistent provider-level conversion deviation, with
  elevated timeout/error rates and retry or queue pressure across several sibling cohorts. It
  should measure payment-level conversion separately from attempt-level behavior and rank impact
  by business exposure.
- **What the investigation should conclude:** Provider P2 degradation is the leading explanation,
  supported by provider-wide first-party timeout/latency evidence and healthy comparison providers.
  It should cite the evidence and recommend investigating P2 or rerouting eligible traffic, not
  silently remediate it.
- **What is a WRONG answer:** Calling this a single merchant, country, issuer, or payment-method
  problem; treating every retry as a separate lost customer payment; asserting a cause without
  discriminating evidence; or ignoring the provider-wide sibling comparison.

## 2. `issuer-overdecline` - Issuer-specific over-decline

- **What is injected:** Issuing bank X begins rejecting otherwise normal payment traffic. A
  representative strength is issuer-decline share `0.10 -> 0.60` and a material payment-level
  conversion drop for Bank X.
- **Affected cohort dimensions:** `{ "issuing_bank": "bank-x" }`. The bank is observed through
  more than one healthy provider so issuer and provider are separable.
- **What the detector should find:** A bank-specific deviation replicated across the providers
  serving Bank X, while those providers remain healthy for other issuers. Decline reason and
  payment-level comparisons should isolate the issuer dimension.
- **What the investigation should conclude:** Bank X over-decline is the leading explanation,
  with provider siblings and non-Bank-X traffic as the discriminating evidence. Confidence may be
  high only when those comparisons support it.
- **What is a WRONG answer:** Declaring a global provider outage from one route, declaring all
  card traffic broken, blaming the merchant without evidence, or using issuer correlation from a
  single provider as proof when the comparison traffic is absent.

## 3. `country-method-interaction` - Country by payment-method interaction

- **What is injected:** A wallet authorization path fails only for Colombian traffic. A
  representative strength is wallet approval conversion `0.91 -> 0.45` in Colombia while the
  same wallet works in Mexico and cards work in Colombia.
- **Affected cohort dimensions:** `{ "country": "CO", "payment_method": "wallet" }`.
- **What the detector should find:** The intersection of country and method, with healthy sibling
  cohorts for `wallet` outside Colombia and for other methods inside Colombia. It should not stop
  at either parent dimension.
- **What the investigation should conclude:** A Colombia-wallet interaction is the most specific
  supported localisation, followed by investigation of the country-specific wallet route or
  configuration. The explanation should preserve the healthy sibling comparisons.
- **What is a WRONG answer:** Calling it a global wallet outage, a global Colombia outage, or a
  provider outage without evidence; localising to only `country` or only `payment_method`; or
  claiming the interaction from a single failing row with no siblings.

## 4. `fine-grained-combination` - Previously unseen fine-grained combination

- **What is injected:** A failure affects a combination not represented by a hard-coded incident
  rule: Merchant A traffic through P2, Mastercard, Colombia, Bank X, and card. A representative
  strength is approval conversion `0.92 -> 0.55` only for that combination.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-a", "provider": "provider-p2",
  "payment_method": "card", "card_network": "mastercard", "country": "CO", "issuing_bank":
  "bank-x" }`.
- **What the detector should find:** The affected intersection through general cohort search,
  with parent and sibling comparisons showing which dimensions materially narrow the change. It
  must not require this exact combination to have been named in code first.
- **What the investigation should conclude:** A bounded, evidence-backed nested explanation at
  the finest level supported by volume and comparisons. If the final provider and issuer evidence
  is confounded, it must preserve that ambiguity instead of over-localising causality.
- **What is a WRONG answer:** Returning only a pre-programmed "fine-grained" label, claiming a
  global P2 or Mastercard problem when siblings are healthy, inventing a causal explanation from
  the scenario name, or selecting a tiny leaf that the evidence cannot support.

## 5. `retry-amplification` - Retry storm and queue amplification

- **What is injected:** An initial provider timeout for Merchant B causes payment retries, queue
  buildup, and elevated attempt volume. A representative strength is `1.35` attempts per payment,
  with queue depth increasing from `40` to `350` while payment-level conversion degrades.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-b", "provider": "provider-p2" }`.
- **What the detector should find:** A provider/merchant failure plus retry amplification and
  queue pressure. It must report customer-payment conversion separately from attempt conversion and
  identify the underlying failure rather than treating amplification as a second population of
  customers.
- **What the investigation should conclude:** The initial timeout or service failure is causing
  retries and queue growth that amplify load. The next action is to investigate retry policy,
  queue health, and the provider path, with any remediation left to the TAM.
- **What is a WRONG answer:** Counting 1,350 attempts as 1,350 lost payments, reporting retry
  amplification as the only root cause, ignoring queue evidence, or recommending a global method
  shutdown without checking the underlying failure.

## 6. `normal-traffic-spike` - Normal high-volume traffic spike

- **What is injected:** No payment failure is injected. Merchant A receives an expected seasonal
  or campaign traffic increase of roughly `2.5x`, while payment-level conversion and operational
  health remain within contextual baseline bounds.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-a" }`.
- **What the detector should find:** Increased volume as an expected observation, not a high-
  priority conversion incident. It should avoid alerting on volume alone and retain the traffic
  context for later comparison.
- **What the investigation should conclude:** There is no supported degradation to diagnose; if a
  low-level anomaly is surfaced, it should be explicitly described as a normal traffic change with
  no material business incident.
- **What is a WRONG answer:** Raising a high-priority incident solely because volume is dramatic,
  inventing a provider or issuer failure, or suppressing a real conversion drop merely because a
  similar volume spike occurred before.

## 7. `high-impact-small-percentage` - High-impact small conversion change

**Demo status: GUARANTEED.**

- **What is injected:** A small approval regression affects the high-volume Merchant A. A
  representative strength is payment conversion `0.920 -> 0.895` (2.5 percentage points) across
  a large attempted value, producing about `$25,000/hour` GMV at risk.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-a" }`.
- **What the detector should find:** A persistent, economically material Merchant A incident even
  though the percentage delta is modest. It should calculate impact from volume and expected
  conversion, keep severity independent from diagnostic confidence, and place the incident in
  business-priority order.
- **What the investigation should conclude:** The change has high business priority and deserves
  immediate TAM attention. It should state what evidence supports the likely cause and what remains
  uncertain without using the small percentage delta to dismiss the incident.
- **What is a WRONG answer:** Calling it harmless because `2.5%` sounds small, assigning low
  priority because the statistical deviation is less dramatic, or ranking it below
  `dramatic-low-volume-anomaly` merely because that scenario has a larger percentage change. The
  evaluator must fail that ordering.

## 8. `dramatic-low-volume-anomaly` - Dramatic low-volume anomaly

- **What is injected:** A tiny Merchant D cohort falls from `0.93 -> 0.40` for only eight payments,
  with about `$120` of estimated impact.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-d" }`.
- **What the detector should find:** A statistically notable but low-business-impact anomaly,
  subject to minimum-volume and persistence policy. If surfaced, it should receive lower business
  priority than the high-impact Merchant A change.
- **What the investigation should conclude:** The percentage movement is dramatic but the sample is
  too small for urgent broad action. It should request more evidence or continued observation and
  keep the economic impact visible.
- **What is a WRONG answer:** Treating percentage change as sufficient for critical priority,
  claiming a confident root cause from eight payments, or ranking it above
  `high-impact-small-percentage`.

## 9. `provider-issuer-confounded` - Provider versus issuer observational confounder

**Demo status: GUARANTEED.**

- **What is injected:** Bank X traffic is observed only through Provider P2 in the window, and the
  P2/Bank-X slice degrades. A representative strength is payment conversion `0.92 -> 0.64`, with
  elevated timeouts and issuer declines.
- **Affected cohort dimensions:** `{ "provider": "provider-p2", "issuing_bank": "bank-x" }`.
- **What the detector should find:** The affected provider/issuer cohort and the deterministic
  fact that the dimensions are observationally inseparable in the current window. It should not
  manufacture a comparison that the data does not contain.
- **What the investigation should conclude:** **Not a confident single cause.** It must provide a
  leading hypothesis, name a competing explanation, and identify the missing evidence that would
  separate them: P2 traffic from another issuer or Bank X traffic through another provider. The
  recommended action is to collect that discriminating evidence before broad rerouting. The
  evaluator scores a confident single-cause answer as a FAILURE even if it names the injected
  provider correctly.
- **What is a WRONG answer:** "Provider P2 is definitely the cause", "Bank X is definitely the
  cause", a fabricated probability, or any high-confidence single-cause result that omits the
  competing explanation and missing comparison. This is the most important scoring rule in the
  catalogue.

## 10. `simultaneous-incidents` - Multiple independent incidents

- **What is injected:** Two failures begin in the same event-time window: instance A degrades
  Provider P1 across its normal traffic, while instance B causes Bank Y over-declines on another
  merchant/provider route. They are independently observable and have separate impact.
- **Affected cohort dimensions:** Instance A `{ "provider": "provider-p1" }`; instance B
  `{ "issuing_bank": "bank-y" }`. Each instance has its own C6 record and diagnosis.
- **What the detector should find:** Two incident records with separate cohort localisation,
  timelines, impact, and lifecycle state. A broad provider anomaly must not absorb the issuer
  incident.
- **What the investigation should conclude:** Two bounded investigations with distinct leading
  hypotheses and evidence trails. It may note temporal overlap but must preserve the independent
  causes and priorities.
- **What is a WRONG answer:** Merging both failures into one root cause, assigning Bank Y's
  over-declines to P1 without comparison evidence, dropping the lower-volume incident, or using
  one diagnosis to explain both records.

## 11. `infrastructure-deployment` - Infrastructure or deployment regression

- **What is injected:** A payment-router deployment changes behavior for Merchant B across its
  providers, causing elevated application errors and latency. A representative strength is error
  rate `0.01 -> 0.12` beginning immediately after the deployment.
- **Affected cohort dimensions:** `{ "merchant_id": "merchant-b" }`. The causal operational
  dimension is deployment/service telemetry, not one of the six payment cohort dimensions.
- **What the detector should find:** Merchant-wide payment symptoms across provider siblings, with
  a matching service/runtime or deployment signal. It should preserve operational telemetry in the
  evidence path.
- **What the investigation should conclude:** The deployment or application/runtime path is the
  leading explanation, supported by timing and cross-provider scope. The next action is to inspect
  the deployment and service health rather than immediately blaming a provider.
- **What is a WRONG answer:** Calling a provider outage because one provider has the most volume,
  ignoring the deployment timestamp, claiming a payment-dimension cause when all providers shift,
  or automatically rolling back production.

## 12. `external-status-disagreement` - First-party degradation versus healthy status page

- **What is injected:** Provider P3's first-party payment telemetry degrades for a cohort while
  its public status endpoint reports healthy. A representative strength is timeout rate
  `0.04 -> 0.28` and a corresponding conversion drop.
- **Affected cohort dimensions:** `{ "provider": "provider-p3" }`.
- **What the detector should find:** The internal conversion and operational deviation, independent
  of the external status response. The external source is optional corroboration, not a gate on
  incident creation.
- **What the investigation should conclude:** First-party evidence remains authoritative and P3
  is a supported leading hypothesis. The healthy status page is recorded as contradicting or
  non-confirming external evidence, not as proof that the internal incident is false.
- **What is a WRONG answer:** Dismissing the incident because the status page says healthy,
  replacing first-party measurements with external claims, or failing the entire diagnosis when the
  external adapter is unavailable.

## Evaluator expectations

The evaluator consumes a completed diagnosis and the quarantined C6 record only after the run. It
checks exact dimension-value pair overlap, the confounded uncertainty rule, and declared relative
priority relations. It does not match free text to a scenario ID and it never feeds a verdict back
to the diagnostic path.
