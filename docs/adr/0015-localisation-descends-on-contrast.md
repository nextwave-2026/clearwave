# 0015 - Localisation descends on sibling contrast, not on depth

## Status

Accepted

## Context

Detection must name where a degradation lives, across combinations of merchant, provider, payment method, card network, country and issuing bank. A hard-coded catalogue of incident types cannot do this, and the full cross product is too large to materialise.

Two obvious ranking rules for descending the dimension lattice both fail, and both failed in practice during implementation:

- ranking children by absolute drop promoted an innocent issuing bank into a provider-and-country incident, because once a cohort has collapsed, ordinary noise inside it beats the parent;
- ranking children by z-score fails the other way, because z grows with sample size, so the larger diluted parent scores more strongly than the child holding the whole degradation, and every incident is reported as "everything is a bit down".

## Decision

Descend on contrast. A dimension enters the reported cohort only when one of its values is materially worse than the next-worst sibling value inside the current cohort, by at least `LOCALISE_MIN_SEPARATION`.

If every issuing bank behind a degraded provider is equally degraded, the issuer does not discriminate and is not part of the story. A dimension with a single observed value offers no contrast and is skipped. The descent records the winning dimension, the separation, and the runner-up at each step.

## Alternatives considered

- Rank by absolute drop - rejected: over-specifies into an arbitrary child of an already-collapsed cohort. Covered by a regression test.
- Rank by z-score - rejected: under-specifies to the parent every time, for the sample-size reason above.
- A fixed dimension order - rejected: it presumes which dimension matters, which is the hard-coded catalogue the product baseline forbids, and it would fail any combination not anticipated.

## Consequences

The reported cohort is the most specific one the evidence supports rather than the finest slice available, which is what the product baseline asks for explicitly. No dimension combination is encoded anywhere, only the rule for descending one, so a combination nobody anticipated is still located. Two regression tests hold the boundary: a provider degraded in one country is reported as provider and country, and a provider degraded in every country is reported as the provider alone.
