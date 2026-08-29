# Payment-operations failure modes

This taxonomy gives L4 operational hypothesis classes. A class is a possible explanation, not a diagnosis. The agent must ground every assertion in C2 responses and cite the exact `query_id`. A missing signal does not contradict a hypothesis; use the contradiction rules from ADR 0007.

## 1. Payment-provider degradation

### What it is

A provider or provider connection is failing, slowing, or refusing work for reasons in the provider path. The fault may be broad or limited to a particular provider, connection, capability, or traffic slice.

### Signature evidence

- `cohort_metrics` shows lower payment-level and attempt-level approval conversion in the provider cohort; a widening gap can indicate that retries or fallback attempts are changing the two views.
- `cohort_compare` shows the same provider affected across its sibling merchants, methods, countries, networks, or issuers, while alternate providers or the parent cohort are healthier.
- `decline_breakdown` shows a shift toward `provider_error`, `processing_error`, `timeout`, or `rate_limited` relative to the cohort baseline. A raw provider code carried in the underlying event can corroborate the pattern.
- `operational_metrics` shows elevated tail latency, `timeout_rate`, or `error_rate` for the provider cohort. `service_health` may be degraded even when `runtime_health` is healthy.
- `retry_stats` shows deeper retries, a higher `attempts_per_payment` or `retry_amplification_factor`, and rising queue depth or delay. `external_status` can corroborate, but is optional.

### Contradicting evidence

- `cohort_compare` shows the same provider has normal conversion and decline mix for independent sibling cohorts while the deviation follows one issuer, country, method, or network across providers.
- `operational_metrics` shows stable latency, timeouts, errors, service health, and runtime health during the whole affected window, and `decline_breakdown` is dominated by customer or issuer reasons rather than technical reasons.
- `cohort_compare` shows the affected provider is healthy where the same route is used by other cohorts, with no provider-scoped separation in the observed data.
- `confounding_check` or another comparison shows provider and another dimension are inseparable; that does not rule this class out, but it prevents attributing the cause to the provider alone.

### Commonly confused with

- **Issuing-bank over-decline:** one issuer may be concentrated on one provider, making the same conversion drop look provider-specific.
- **Routing or configuration problems:** a bad route can expose a healthy provider incorrectly or send the wrong traffic to it.
- **Infrastructure or capacity faults:** local queues and timeouts can resemble a provider outage.
- **Retry amplification:** retries are often an effect of provider failures, not proof of their root cause.

## 2. Issuing-bank over-decline

### What it is

An issuer is refusing an unusually large share of otherwise attempted payments, through issuer policy, issuer risk controls, issuer availability, or issuer-side processing. This class is about a population-level issuer pattern, not a single customer's legitimate refusal.

### Signature evidence

- `cohort_metrics` shows payment-level and attempt-level approval conversion falling in the issuer cohort, with the decline concentrated on that issuer rather than on all traffic.
- `cohort_compare` shows the issuer's behavior repeated across more than one provider or method, while other issuers using those providers remain near their parent or sibling conversion.
- `decline_breakdown` shows increased `issuer_decline`, `do_not_honor`, `issuer_unavailable`, or sometimes `suspected_fraud`; the reason mix should be compared with its baseline rather than treated as a diagnosis by itself.
- `operational_metrics` is healthy for the relevant provider and service, with no matching latency or timeout change. `retry_stats` may remain near baseline unless the issuer refusals trigger retries.
- `confounding_check` can show whether issuer and provider are separable. If they are inseparable, the evidence supports both explanations but does not select one.

### Contradicting evidence

- `cohort_compare` shows the same issuer converting normally through an independent provider while all issuers on one provider degrade.
- `decline_breakdown` and `operational_metrics` show a broad shift to `timeout`, `provider_error`, high latency, or local service errors without a corresponding issuer concentration.
- `cohort_compare` shows the degradation follows one country, method, or network across issuers, and other issuers in the same target slice show the same drop.
- Only a tiny set of unrelated individual card conditions changes while the issuer population and its sibling cohorts remain normal; that does not support an issuer-wide cause.

### Commonly confused with

- **Payment-method failure:** an issuer may refuse one method or authentication path while accepting another.
- **Card-network-specific failure:** issuer traffic may be concentrated on one network.
- **Payment-provider degradation:** the issuer and provider may have only one observed pairing.
- **Customer-side card conditions:** legitimate `insufficient_funds`, expired, invalid, or restricted cards can create issuer-like counts without an issuer outage.

## 3. Payment-method failure

### What it is

A payment method or its integration path is failing independently of a single issuer, country, or provider. The problem may be a capability, authentication flow, method-specific API, or broad method processing defect.

### Signature evidence

- `cohort_metrics` shows both payment-level and attempt-level conversion lower for the method, with a corresponding shift in failed attempts.
- `cohort_compare` shows the same method degraded across multiple providers or countries, while sibling methods in the same parent cohort remain healthy.
- `decline_breakdown` shows method-relevant reasons such as `authentication_required`, `authentication_failed`, `currency_not_supported`, `invalid_card`, or a technical reason; the distribution and baseline shift matter more than one code.
- `operational_metrics` shows method-path latency, errors, or timeouts, or a shared service degradation associated with the method. `retry_stats` shows whether method failures are being multiplied by retries and queue delay.
- `drilldown` can establish that the observed path stops at the method level rather than at a narrower country, network, issuer, or provider cohort.

### Contradicting evidence

- `cohort_compare` shows the method is healthy in other countries and providers while the drop is confined to one country-provider or issuer slice.
- A different method using the same provider has the same technical degradation, or the provider's operational metrics alone explain the failures.
- `decline_breakdown` is dominated by one issuer's refusal reasons, with no method-wide shift, and `confounding_check` identifies an inseparable issuer or provider pairing.
- `operational_metrics` shows no method-path change and `retry_stats` remains at baseline while only a narrow dimension has changed.

### Commonly confused with

- **Country or region-specific failure:** a method can be supported globally but fail in one geography or currency.
- **Routing or configuration problems:** a method can be sent to an incompatible provider or stale connection.
- **Authentication conditions:** authentication codes can be a method symptom or a customer interaction issue.
- **Issuing-bank over-decline:** issuer policy can affect one method disproportionately.

## 4. Country or region-specific failure

### What it is

A payment flow fails for a geographic cohort or regional operating condition while comparable traffic outside that region remains healthy. The cause can be local provider coverage, regulation, currency, routing, connectivity, or a regional integration dependency.

### Signature evidence

- `cohort_metrics` shows lower payment-level and attempt-level approval conversion for the country or region cohort.
- `cohort_compare` shows the same provider and method perform normally in other countries, while the target geography is worse than siblings and parent; a method-country interaction is especially informative.
- `decline_breakdown` shows a geographic shift in `currency_not_supported`, `restricted_card`, issuer reasons, or technical reasons. The change must be compared with the geographic baseline.
- `operational_metrics` shows latency, timeout, or error concentration for the regional cohort; `retry_stats` shows whether regional delays are growing queue depth or retry amplification.
- `drilldown` and `confounding_check` help establish whether geography is independently observed or inseparable from provider, method, network, or issuer.

### Contradicting evidence

- `cohort_compare` shows the same country is healthy through other providers and methods while the provider is degraded for every country.
- The same method, provider, and network have the same conversion and decline shift outside the target region, arguing for a global cause.
- `operational_metrics` shows a provider or service-wide failure with no regional difference, or `decline_breakdown` shows a uniform issuer refusal across geographies.
- `confounding_check` shows the only traffic in the region uses one provider or issuer; this does not contradict a regional cause, but it does contradict a confident regional attribution without more comparison.

### Commonly confused with

- **Payment-method failure:** the method may be unavailable only where local capability differs.
- **Routing or configuration problems:** a regional route or currency setting may be wrong.
- **Issuing-bank over-decline:** issuer populations often vary by geography.
- **Card-network-specific failure:** network mix can change with country and make a network issue look geographic.

## 5. Card-network-specific failure

### What it is

A card network's authorization or processing path is degraded for a population of traffic, independently of the other networks available in the same operating context. It may be network-wide or limited to a network-provider-country interaction.

### Signature evidence

- `cohort_metrics` shows payment-level and attempt-level conversion falling for one card network.
- `cohort_compare` shows other networks using the same provider, method, country, and merchant are healthier, while the affected network's pattern repeats across provider or merchant siblings where traffic exists.
- `decline_breakdown` shows a network-specific shift in issuer, authentication, or technical reasons. `provider_raw_code`, preserved unparsed, can corroborate network response behavior when the normalised reason is broad.
- `operational_metrics` may show latency or errors on a network-specific path; `retry_stats` shows whether the path is causing repeated attempts and queue delay.
- `drilldown` and `confounding_check` identify whether network is a supported independent boundary or merely a label for one issuer-provider route.

### Contradicting evidence

- `cohort_compare` shows all networks on the same provider degrade together while the same network is healthy through another provider.
- The drop follows one issuer or country across networks, and `decline_breakdown` shows issuer-side reasons rather than a network-specific shift.
- `operational_metrics` shows a service-wide or provider-wide failure with no network separation.
- The target network has too little traffic for a reliable comparison; low volume is a limitation, not evidence for this class.

### Commonly confused with

- **Issuing-bank over-decline:** issuer portfolios can be concentrated on one network.
- **Country or region-specific failure:** network mix often differs by geography.
- **Payment-method failure:** the card method can fail while the network is healthy.
- **Payment-provider degradation:** one provider-network connection can be the narrow failing path.

## 6. Routing or configuration problems

### What it is

Traffic is sent to the wrong provider or connection, or a route, credential, capability, currency, weight, or policy is incorrect or stale. The selected provider may be healthy in other traffic; the fault is in how the system selects or configures the path.

### Signature evidence

- `drilldown` localises the drop to a route-shaped provider, method, country, network, or merchant slice rather than to every use of the provider.
- `cohort_compare` shows sibling traffic using another route is healthy, or shows the same provider healthy for traffic that does not use the suspect configuration. A narrow route cohort can have lower payment and attempt conversion while its parent is less affected.
- `decline_breakdown` shifts toward `currency_not_supported`, `rate_limited`, `provider_error`, `processing_error`, `invalid_card`, or `other` in a pattern consistent with an incompatible or stale configuration.
- `operational_metrics` identifies the deployment associated with the cohort and shows service/error/timeout behavior; a route-specific problem can have healthy runtime metrics while the selected path fails.
- `retry_stats` can reveal retries repeatedly selecting the same bad route, with increased amplification and queue depth. `confounding_check` identifies whether the route dimensions are inseparable.

### Contradicting evidence

- `cohort_compare` shows every provider and route fails in the same way, with no unaffected comparison path, and `operational_metrics` shows a shared service or runtime fault.
- The provider is degraded for independent traffic that uses a different configuration, making provider health a better explanation than selection.
- `decline_breakdown` is consistently issuer-side across routes, with stable service and routing observations.
- There is no route, deployment, or configuration boundary in the observed data; absence of a visible boundary limits attribution rather than proving configuration is correct.

### Commonly confused with

- **Payment-provider degradation:** both can concentrate failures on one provider.
- **Application or deployment faults:** a release can alter route selection or credentials.
- **Country or method failure:** route capability gaps often follow those dimensions.
- **Retry amplification:** a retry policy may keep selecting the same bad route and amplify the original fault.

## 7. Retry amplification

### What it is

A failure causes the system to make multiple attempts for the same customer payment, increasing request load, queue pressure, and observed failures. It is primarily a mechanism that can amplify a root cause, not proof of which component started it.

### Signature evidence

- `retry_stats` shows `attempts_per_payment` and `retry_amplification_factor` above baseline, more `retried_payments`, deeper retry distribution, and rising queue depth or delay.
- `cohort_metrics` shows attempt-level conversion worse than payment-level conversion, or both levels falling when retries do not recover payments. Payments remain the customer-loss denominator; attempts expose load amplification.
- `decline_breakdown` shows repeated failure reasons across attempts, often `timeout`, `provider_error`, or `processing_error`, but the reason identifies a trigger candidate, not the retry mechanism itself.
- `operational_metrics` shows latency, timeout, or error rates rising with the retry period. Service and runtime health can be normal initially and degrade as load accumulates.
- `cohort_compare` shows amplification concentrated in the affected cohort rather than a simple platform-wide volume increase.

### Contradicting evidence

- `retry_stats` shows attempts per payment, retry depth, amplification, queue depth, and delay at their normal baseline while conversion falls.
- `cohort_metrics` shows payment and attempt counts moving together with one attempt per payment, and `operational_metrics` shows no corresponding queue or latency change.
- The apparent volume increase is explained by more distinct payments, not more attempts per payment; that supports a genuine traffic surge instead.
- A low-level failure may still exist without amplification, so normal retry statistics contradict the amplification class but do not rule out provider, issuer, or method causes.

### Commonly confused with

- **Payment-provider degradation:** provider timeouts often trigger retries; the provider can be the root cause and amplification the secondary effect.
- **Infrastructure or capacity faults:** queues and latency can be the cause of retries or their consequence.
- **Genuine volume surge:** more payments and more attempts are different observations.
- **Application or deployment faults:** a changed retry policy can create amplification without changing the original failure rate.

## 8. Application or deployment faults

### What it is

Application behavior, code, dependency handling, or a newly deployed version changes payment processing for one or more cohorts. The fault can be merchant-specific, service-specific, or broad across provider paths.

### Signature evidence

- `cohort_compare` shows conversion falling across provider siblings for the same merchant, method, or region, rather than following one provider or issuer.
- `operational_metrics` shows elevated `error_rate`, latency, or `timeout_rate`, a degraded `service_health`, or a deployment identity that aligns with the onset. `runtime_health` may remain healthy when the defect is logical.
- `decline_breakdown` shifts toward `processing_error`, `provider_error`, `timeout`, `authentication_failed`, `duplicate`, or `other` depending on the application behavior.
- `retry_stats` shows a changed retry depth or amplification pattern and queue growth if the release altered error handling or backoff.
- `incident_history` can show recurrence after a prior deployment or a repeated application-shaped pattern, but history is corroboration, not proof.

### Contradicting evidence

- `cohort_compare` shows only one provider, issuer, country, method, or network affected while the application and its deployment serve unaffected siblings normally.
- `operational_metrics` shows no deployment change, stable service and runtime health, and no increase in errors, latency, or timeouts during onset.
- `decline_breakdown` shows a clean issuer-side or customer-side shift across providers without application errors.
- `retry_stats` and queue measures remain normal, and a provider-specific comparison or `external_status` better explains the timing.

### Commonly confused with

- **Payment-provider degradation:** one provider may carry most traffic, creating a false application-wide appearance.
- **Routing or configuration problems:** a release can change routing without the provider being unhealthy.
- **Infrastructure or capacity faults:** local saturation produces application-facing errors and latency.
- **Payment-method failure:** a method integration bug can look like a method-wide external outage.

## 9. Infrastructure or capacity faults

### What it is

Compute, network, storage, queue, runtime, or shared service capacity is insufficient or unhealthy for the payment workload. The fault is local to the platform or an operational dependency, not necessarily to a payment dimension.

### Signature evidence

- `operational_metrics` shows broad latency, error, or timeout elevation, degraded `service_health` or `runtime_health`, and the same deployment or service problem across provider siblings.
- `retry_stats` shows queue depth and delay increasing, deeper retries, and a higher `retry_amplification_factor`; pressure can worsen as retries add load.
- `cohort_metrics` shows both payment-level and attempt-level conversion falling across a broad parent, while `cohort_compare` shows several providers, methods, countries, or networks affected together.
- `decline_breakdown` shifts toward `timeout`, `processing_error`, `provider_error`, or `rate_limited`, with no single issuer or customer reason explaining the breadth.
- `drilldown` stops at a broad merchant, service, or parent cohort rather than isolating one payment dimension. The returned operational observations remain the evidence for the infrastructure hypothesis.

### Contradicting evidence

- `cohort_compare` shows a single provider or issuer affected while other traffic sharing the same service and runtime remains healthy.
- `operational_metrics` shows healthy service and runtime, normal latency, errors, and timeouts, with no queue growth in `retry_stats`.
- `decline_breakdown` is dominated by issuer, authentication, or card-state reasons and the affected cohort is narrow.
- A deployment-aligned, route-specific change explains the observations without broad resource pressure.

### Commonly confused with

- **Payment-provider degradation:** both create timeouts, retries, and queue growth.
- **Application or deployment faults:** logical errors can look like a saturated service.
- **Retry amplification:** retries can create capacity pressure rather than merely report it.
- **Routing or configuration problems:** a bad route can overload one connection or queue.
