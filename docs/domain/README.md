# Payments operations domain knowledge pack

**Pack version:** `domain-v1`

This pack is explicit, inspectable payments-operations knowledge for the L4 investigation agent. It explains failure modes, decline semantics, high-value discriminators, and proportionate recommendations. It complements the deterministic observations returned by the C2 evidence tools; it does not replace them.

## General knowledge only

This pack contains no scenario answers, scenario identifiers, fixture values, merchant names, provider names, issuer names, or hidden truth. It must remain useful when the simulator produces a degradation combination nobody has seen before. Do not add a named example merely because it makes a rehearsed demonstration easier to explain. ADR 0012 is a hard boundary: scenario identifiers never reach L4, and this pack must not encode their answers indirectly.

Use generic terms such as a provider, an issuer, a merchant, a country, or a network when an example is needed. Never use a fixture value as an example.

## How L4 consumes it

The agent adapter injects this pack as reference material into the investigation prompt. The agent still receives only the incident record and evidence returned through the gateway-backed C2 tools. The pack is guidance for forming, comparing, and communicating hypotheses; it is not evidence and cannot justify a claim without a cited `query_id`.

The files are versioned with the repository and are readable by operators, reviewers, and judges. A change to domain guidance is therefore inspectable in the same history as the investigation behavior. The authoritative machine-facing shapes remain [`docs/contracts/evidence-tools.md`](../contracts/evidence-tools.md), [`docs/contracts/canonical-event.md`](../contracts/canonical-event.md), and [`docs/contracts/investigation-result.md`](../contracts/investigation-result.md).

## Maintenance rule

Every addition must pass this test:

> Would this still be true and useful for a degradation combination nobody has ever seen?

If not, delete it. Do not add scenario-specific branches, answers, identifiers, or fixture-derived wording. Keep decline names synchronized with the closed vocabulary in `detector/schema.py`, keep query names synchronized with the C2 contract, and preserve the rule from ADR 0007: absence of evidence is not a contradiction, so a hypothesis may be ruled out only with cited contradicting evidence.
