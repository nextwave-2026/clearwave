# Architecture decision records

These records lock design decisions before and during implementation. Each ADR uses the same readable format: **Status**, **Context**, **Decision**, **Alternatives considered**, and **Consequences**. Status is `Accepted` for every decision below.

The layer sequence is L1 merchant emission, L2 ingestion and normalisation, L3 deterministic detection, L4 investigation, and L5 surfaces and escalation. L4 Investigation is owned by `derek` and corresponds to W3; L2 and L3 are owned by `andres` and correspond to W2.

## L4 Investigation records - `derek`

- [0001 - Investigation assembles its own evidence bundle](0001-investigation-assembles-evidence-bundle.md)
- [0002 - Diagnostic confidence belongs to Investigation](0002-diagnostic-confidence-belongs-to-investigation.md)
- [0003 - External corroboration is an evidence tool](0003-external-corroboration-is-an-evidence-tool.md)
- [0004 - L4 consumes no Kafka](0004-l4-consumes-no-kafka.md)
- [0005 - Confounding detection is deterministic](0005-confounding-detection-is-deterministic.md)
- [0006 - Evidence gateway owns query identity and calls](0006-evidence-gateway-owns-query-identity-and-calls.md)
- [0007 - Ruled-out hypotheses require contradiction](0007-ruled-out-hypotheses-require-contradiction.md)
- [0008 - Headless Pi is the constrained agent runtime](0008-headless-pi-constrained-agent-runtime.md)
- [0009 - Investigations are bounded](0009-investigations-are-bounded.md)
- [0010 - Every investigation emits a result](0010-every-investigation-emits-a-result.md)
- [0011 - One versioned run per incident](0011-one-versioned-run-per-incident.md)
- [0012 - Scenario identifiers never reach L4](0012-scenario-identifiers-never-reach-l4.md)

## L2 and L3 Detection plane records - `andres`

The layer they describe is documented in [detection-plane.md](../detection-plane.md), with diagrams.

- [0013 - Detection qualifies an incident behind four floors](0013-detection-floors-not-a-single-threshold.md)
- [0014 - Severity is bounded by money, not only weighted by it](0014-severity-is-bounded-by-money.md)
- [0015 - Localisation descends on sibling contrast, not on depth](0015-localisation-descends-on-contrast.md)
- [0016 - All detection arithmetic buckets on event time](0016-event-time-bucketing.md)
- [0017 - Value is priced per payment, never per attempt](0017-value-is-priced-per-payment.md)
- [0018 - Native source shapes are normalised through a mapper registry](0018-native-shapes-through-a-mapper-registry.md)
- [0019 - The canonical model closes the decline vocabulary and preserves the raw code](0019-canonical-vocabulary-with-preserved-raw-code.md)
