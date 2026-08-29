# 0017 - Value is priced per payment, never per attempt

## Status

Accepted

## Context

One customer payment can produce several provider attempts. Financial impact can be computed over attempts or over payments, and the two diverge exactly when a degradation causes retries.

Pricing attempts inflates the estimated loss in proportion to the retry amplification, which peaks at the moment the number is being read off a dashboard and quoted to a judge. It also double-counts: a payment retried three times and finally failing is one lost sale, not three.

## Decision

Every monetary figure is computed over distinct payments. A payment contributes its value once, regardless of how many attempts it produced. Attempt-level measurements remain available and are reported separately, because the gap between payment-level and attempt-level conversion is itself evidence.

All figures are labelled GMV at risk and carry their assumptions in the response, so no consumer can render them as platform revenue.

## Alternatives considered

- Price per attempt - rejected: inflates loss during exactly the incidents that matter, and double-counts a retried payment.
- Price per approved attempt only - rejected: it cannot express the value that was never approved, which is the quantity of interest.
- Let the investigation agent compute impact - rejected: the product baseline forbids the model inventing financial calculations, and a second arithmetic path would diverge from the first.

## Consequences

Retry amplification raises attempt counts and leaves the money figure correct. A retry storm shows up as a separate first-class signal instead of contaminating the impact estimate. Because detection owns the arithmetic, investigation and surfaces cite one figure rather than deriving two.
