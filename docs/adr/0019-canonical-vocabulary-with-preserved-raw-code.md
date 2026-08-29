# 0019 - The canonical model closes the decline vocabulary and preserves the raw code

## Status

Accepted

## Context

Decline mix is one of the strongest discriminators between a provider problem and an issuer problem, and comparing it across providers requires the reasons to mean the same thing. Providers return their own codes, in their own vocabularies, with their own overlaps.

Left as free text, the distribution becomes a list of near-synonyms that cannot be compared or aggregated, and the strongest available discriminator is lost. Discarding the provider's own code instead loses the detail an operator needs when escalating to that provider.

## Decision

Normalisation maps each provider's native code into a closed vocabulary defined in `detector/schema.py`. A reason outside the vocabulary is rejected rather than passed through. The provider's own code travels alongside in `provider_raw_code`, preserved verbatim, and is never parsed for arithmetic.

A failed status must carry a reason and a successful status must not, so the two cannot drift apart.

Currency is converted once at ingestion using a frozen, versioned table. A live rate would make a replay produce a different number than the original run. An unknown currency is an error rather than a silent pass-through.

## Alternatives considered

- Free-text decline reasons - rejected: the distribution stops being comparable and the discriminator is lost.
- Closed vocabulary with the raw code discarded - rejected: an operator escalating to a provider needs that provider's own code.
- Convert currency at query time against a live rate - rejected: it breaks replay determinism and makes a stored figure unreproducible.

## Consequences

Decline mix is comparable across providers, merchants and countries, which is what lets the investigation distinguish a provider fault from an issuer fault. A provider introducing an unmapped code produces a visible rejection rather than a silently miscounted distribution. Money is reproducible because the rate is part of the versioned configuration.
