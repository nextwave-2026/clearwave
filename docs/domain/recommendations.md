# Investigation and recommendation playbook

L4 advises; it never executes. The system recommends an action, and the account manager remains in control of approving, rejecting, or escalating it. Recommendations must be proportionate to both diagnostic confidence and blast radius. Severity is business priority from Detection; diagnostic confidence is causal evidence from Investigation. They are independent.

A recommendation should be reversible, scoped to the affected cohort where possible, observable after approval, and explicit about its assumptions. A broad action requires stronger evidence than a narrow investigative step. When confidence is low and impact is high, the correct recommendation is the discriminating investigation in [`discriminators.md`](discriminators.md), not a mitigation.

## 1. Payment-provider degradation

**Safe investigative actions**

- Compare the affected provider with sibling providers and the parent using `cohort_compare`, holding merchant, method, geography, network, and issuer filters constant where traffic exists.
- Inspect `decline_breakdown` for technical-reason shifts and `operational_metrics` for latency, timeouts, errors, service health, runtime health, and deployment identity.
- Check `retry_stats` for queue pressure and retry amplification. Query `external_status` as optional corroboration and record an unavailable or disagreeing source rather than treating it as disproof.
- Use `confounding_check` before attributing a provider cause where provider and issuer, network, or geography are concentrated together.

**Safe mitigations**

- After human approval, reduce or reroute only the affected eligible cohort to a known healthy fallback, with a bounded duration and rollback condition.
- Cap or back off retries for the affected path when amplification is worsening load, while preserving customer-payment identity and monitoring conversion.
- Escalate the cited evidence to the provider and keep the incident under observation.

**Destructive actions to avoid**

- Do not disable the provider globally on a provider-scoped signal; unaffected merchants and payment methods may lose a healthy route.
- Do not reroute all traffic without a comparison of fallback capacity and behavior; this can move the outage or create a second one.
- Do not increase retries blindly; it can multiply queue load and financial exposure.

## 2. Issuing-bank over-decline

**Safe investigative actions**

- Compare the issuer through other providers and compare other issuers through the same provider with `cohort_compare`.
- Inspect `decline_breakdown` for a population-level shift in `issuer_decline`, `do_not_honor`, `issuer_unavailable`, or risk-related reasons.
- Check `operational_metrics` for healthy provider and service behavior, and `confounding_check` for an inseparable provider-issuer pairing.
- Use `incident_history` to identify recurrence, while treating history as context rather than proof of the current cause.

**Safe mitigations**

- Ask the provider or issuer to investigate the cited issuer-level pattern and supply the affected window and raw codes where available.
- If an approved alternate route is known to work for that issuer, propose a narrow, time-bounded route for the affected cohort only, with monitoring and rollback.
- Offer an alternate payment method to affected customers only where product policy permits and the account manager approves.

**Destructive actions to avoid**

- Do not block an issuer globally based on a single merchant or route; legitimate customers and unrelated traffic may be rejected.
- Do not declare an issuer outage from `issuer_decline` alone; customer/card-state conditions and confounding can look the same.
- Do not reroute every issuer or provider before establishing a discriminatory comparison.

## 3. Payment-method failure

**Safe investigative actions**

- Compare the method with sibling methods across providers and countries using `cohort_compare`.
- Use `decline_breakdown` to inspect authentication, capability, customer, and technical reason shifts.
- Inspect `operational_metrics` for the method path and `retry_stats` for method-specific queue or retry behavior.
- Use `drilldown` to test whether the evidence stops at the method level or supports a narrower country, network, issuer, or provider explanation.

**Safe mitigations**

- Present an approved alternate method to the affected cohort while retaining the failing method for unaffected cohorts.
- Correct a verified method capability or authentication configuration through the normal change process, with a rollback plan.
- Pause a narrowly affected method-country or method-provider path temporarily if the evidence and blast radius justify it and the account manager approves.

**Destructive actions to avoid**

- Do not disable the payment method globally; a localized failure does not justify removing a healthy option elsewhere.
- Do not change authentication requirements or bypass customer checks to raise conversion; this can create fraud and compliance risk.
- Do not blame the method when the same method is healthy outside one provider, country, or issuer.

## 4. Country or region-specific failure

**Safe investigative actions**

- Compare the same method and provider in other countries, and compare other methods or providers in the affected country, with `cohort_compare`.
- Inspect `decline_breakdown` for capability, currency, issuer, and technical shifts.
- Use `operational_metrics` for regional latency, timeouts, errors, and service health; use `confounding_check` when one regional route has only one provider or issuer.
- Confirm whether the pattern recurs with `incident_history` without assuming recurrence is healthy or causal.

**Safe mitigations**

- Offer a region-appropriate alternate method or provider only for the affected geography, subject to approved coverage and monitoring.
- Correct a verified currency or regional route configuration through a reviewed, reversible change.
- Temporarily reduce exposure on the affected regional path while preserving unaffected countries.

**Destructive actions to avoid**

- Do not disable a method or provider worldwide because one region is failing.
- Do not reroute all geographies to a fallback whose local capability, currency support, or capacity is unverified.
- Do not infer a regional cause when the only observed traffic is inseparable from one provider or issuer.

## 5. Card-network-specific failure

**Safe investigative actions**

- Compare the affected network with other networks under the same provider, method, country, and merchant using `cohort_compare`.
- Compare the same network through independent providers where available.
- Inspect `decline_breakdown` and `operational_metrics` for network-specific technical, issuer, authentication, latency, and timeout patterns.
- Use `confounding_check` to verify that the network is not merely a proxy for one issuer-provider route.

**Safe mitigations**

- Propose a narrow alternate route or payment method for the affected network after confirming eligibility and fallback health.
- Escalate network-specific evidence to the provider or network operations contact.
- Freeze nonessential routing changes while collecting the comparison that separates a network issue from a provider connection issue.

**Destructive actions to avoid**

- Do not disable the card method or all card networks globally; one network's failure does not justify removing unaffected networks.
- Do not route every network through one fallback without checking its capacity and network coverage.
- Do not attribute the issue to the network when the same network is healthy through another provider and the provider path is the only changed boundary.

## 6. Routing or configuration problems

**Safe investigative actions**

- Use `cohort_compare` to compare the selected path with sibling providers and unaffected cohorts, looking for a failure limited to one route-shaped slice.
- Inspect `operational_metrics` for deployment identity, service health, errors, timeouts, and latency around onset.
- Inspect `decline_breakdown` for `currency_not_supported`, `rate_limited`, and technical reasons consistent with an incompatible route or credential.
- Use `retry_stats` to determine whether retries repeatedly select the same path, and `confounding_check` to identify missing route separation.

**Safe mitigations**

- After approval, restore a previously known-good route or configuration for the smallest affected cohort, with change ownership, expiry, and rollback.
- Freeze unrelated routing changes until the incident is understood.
- Reduce retries to an invalid or rate-limited route and monitor the queue while the configuration is corrected.

**Destructive actions to avoid**

- Do not replace the entire routing policy or reroute all traffic from a single narrow symptom; the blast radius can be larger than the incident.
- Do not rotate or invalidate credentials globally without identifying the affected connection and preserving a tested rollback.
- Do not treat a provider error as proof of bad configuration, or a bad route as proof that the provider is down.

## 7. Retry amplification

**Safe investigative actions**

- Query `retry_stats` and compare `attempts_per_payment`, retry depth, `retry_amplification_factor`, queue depth, and queue delay with baseline.
- Use `cohort_metrics` to keep payment-level conversion separate from attempt-level conversion.
- Inspect `operational_metrics` and `decline_breakdown` to identify the failure that is triggering retries.
- Compare affected and unaffected cohorts with `cohort_compare` to distinguish a local policy change from a genuine traffic surge.

**Safe mitigations**

- Apply a bounded backoff or retry cap to the affected cohort after approval, preserving idempotency and customer-payment tracking.
- Stop retrying a known terminal condition, and drain or throttle a growing queue according to an approved runbook.
- Prefer a healthy alternate path only when the evidence supports it and its capacity is known.

**Destructive actions to avoid**

- Do not disable retries globally; some transient failures depend on a controlled retry to recover.
- Do not purge a queue or replay all attempts without an idempotency and customer-impact plan.
- Do not count attempts as lost payments or use retry amplification alone to name the root cause.

## 8. Application or deployment faults

**Safe investigative actions**

- Use `operational_metrics` to inspect service and runtime health, latency, errors, timeouts, and deployment identity around onset.
- Use `cohort_compare` to see whether all provider siblings for the service or merchant shifted together.
- Inspect `decline_breakdown` for processing, authentication, duplicate, and other integration-shaped changes; use `retry_stats` for changed retry behavior.
- Query `incident_history` for a repeated deployment-shaped pattern, but require current evidence before assigning cause.

**Safe mitigations**

- Pause a rollout or roll back the implicated deployment through the reviewed deployment process, limited to the affected service and with a clear health check.
- Restore a known-good application configuration after human approval and monitor conversion, error rate, and queue behavior.
- Reduce traffic to a failing application path only when a tested fallback exists.

**Destructive actions to avoid**

- Do not roll back unrelated services or all deployments based on timing alone.
- Do not restart or terminate all runtime instances as a first response; this can erase useful evidence and expand downtime.
- Do not bypass validation, authentication, or idempotency checks to suppress application errors.

## 9. Infrastructure or capacity faults

**Safe investigative actions**

- Query `operational_metrics` for service and runtime health, latency percentiles, error and timeout rates, and affected deployment or service.
- Query `retry_stats` for queue depth, queue delay, retry depth, and amplification, then compare the timeline with conversion from `cohort_metrics`.
- Use `cohort_compare` to determine whether the issue spans provider siblings and other dimensions sharing the infrastructure.
- Use `drilldown` to check whether the evidence stops at a broad service or parent cohort rather than one payment dimension.

**Safe mitigations**

- Scale the constrained resource or add capacity through an approved, reversible operational change.
- Throttle or shed only the affected load when necessary, with explicit customer-impact limits and a recovery signal.
- Cap retries and drain queues gradually after capacity is restored; keep a clear rollback and observation window.

**Destructive actions to avoid**

- Do not restart or replace all infrastructure at once; it can turn a partial degradation into a full outage and destroy diagnostic context.
- Do not shed all payment traffic or disable all safeguards to reduce load.
- Do not reroute every provider or merchant until fallback capacity and its own health are established.

## Guardrails for every recommendation

- Include the cited evidence and the uncertainty that remains. A recommendation without a supporting `query_id` is not evidence-backed.
- Name the affected cohort and the proposed blast radius. Prefer the narrowest reversible action.
- Separate a discriminating investigation from a mitigation. When diagnostic confidence is low and impact is high, recommend the specific query in [`discriminators.md`](discriminators.md), not a broad fix.
- Never execute the recommendation automatically. The account manager owns the production decision and should see the expected benefit, risks, expiry, rollback, and monitoring condition.
