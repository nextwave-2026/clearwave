# Architecture decision records

These records lock design decisions before and during implementation. Each ADR uses the same readable format: **Status**, **Context**, **Decision**, **Alternatives considered**, and **Consequences**. Status is `Accepted` for every decision below.

The layer sequence is L1 merchant emission, L2 ingestion and normalisation, L3 deterministic detection, L4 investigation, and L5 surfaces and escalation. L4 Investigation is owned by `derek` and corresponds to W3; L2 and L3 are owned by `andres` and correspond to W2; L5 is owned by `juank` and corresponds to W4.

[`docs/decision-log.md`](../decision-log.md) is the judge-facing index over every ADR below plus
`DECISIONS.md` and the code that implements each call - read it first for the two-minute version
and the reversals; come here for the full record behind any one line of it.

## L4 Investigation records - `derek`

- [0001 - Investigation assembles its own evidence bundle](0001-investigation-assembles-evidence-bundle.md)
- [0002 - Diagnostic confidence belongs to Investigation](0002-diagnostic-confidence-belongs-to-investigation.md)
- [0003 - External corroboration is an evidence tool](0003-external-corroboration-is-an-evidence-tool.md)
- [0004 - L4 consumes no Kafka](0004-l4-consumes-no-kafka.md)
- [0005 - Confounding detection is deterministic](0005-confounding-detection-is-deterministic.md)
- [0006 - Evidence gateway owns query identity and calls](0006-evidence-gateway-owns-query-identity-and-calls.md)
- [0007 - Ruled-out hypotheses require contradiction](0007-ruled-out-hypotheses-require-contradiction.md)
- [0008 - Headless Pi is the constrained agent runtime](0008-headless-pi-constrained-agent-runtime.md) - superseded by ADR 0013
- [0009 - Investigations are bounded](0009-investigations-are-bounded.md)
- [0010 - Every investigation emits a result](0010-every-investigation-emits-a-result.md)
- [0011 - One versioned run per incident](0011-one-versioned-run-per-incident.md)
- [0012 - Scenario identifiers never reach L4](0012-scenario-identifiers-never-reach-l4.md)
- [0013 - Hand-rolled OpenAI loop is the constrained agent runtime](0013-hand-rolled-openai-loop-agent-runtime.md)
- [0022 - OpenAI structured outputs fit bounded investigation](0022-openai-structured-loop-over-coding-agent-harness.md)
- [0025 - Investigation starts on a watch](0025-investigation-starts-on-a-watch.md) - supersedes the claim-only-detected property of ADR 0024

## W1 records - `raul`

- [0014 - W1 raw events share one schema per topic, not one per merchant](0014-w1-raw-events-share-one-schema-per-topic.md) - W1 (`raul`), Proposed

## L2 and L3 Detection plane records - `andres`

The layer they describe is documented in [detection-plane.md](../detection-plane.md), with diagrams.

- [0015 - Detection qualifies an incident behind four floors](0015-detection-floors-not-a-single-threshold.md)
- [0016 - Severity is bounded by money, not only weighted by it](0016-severity-is-bounded-by-money.md) - superseded by ADR 0023
- [0017 - Localisation descends on sibling contrast, not on depth](0017-localisation-descends-on-contrast.md)
- [0018 - All detection arithmetic buckets on event time](0018-event-time-bucketing.md)
- [0019 - Value is priced per payment, never per attempt](0019-value-is-priced-per-payment.md)
- [0020 - Native source shapes are normalised through a mapper registry](0020-native-shapes-through-a-mapper-registry.md)
- [0021 - The canonical model closes the decline vocabulary and preserves the raw code](0021-canonical-vocabulary-with-preserved-raw-code.md)
- [0023 - Severity is relative to the merchant, and recurrence may promote past the money ceiling](0023-severity-is-relative-to-the-merchant-and-promoted-by-recurrence.md) - supersedes ADR 0016
- [0024 - Leading indicators warn early, and nothing is predicted](0024-leading-indicators-warn-early-without-prediction.md) - claim-only-detected property superseded by ADR 0025
- 0026 - An incident record is the current reading, not the first one - claimed by `andres` (`STATUS.md` 2026-08-30T08:35Z), open in PR #88, not yet merged; add the link once it lands

How this plane behaves at Yuno's real volume, and the two changes that would be needed, is in
[scaling.md](../scaling.md).

## L5 Surfaces and escalation records - `juank`

W4 had no ADRs before deliverable 5. Its three records are summarised, each with a diagram, inside
[`docs/decision-log.md`](../decision-log.md) itself rather than as separate files here - that
document is the one place a judge needs to open for the whole decision trail.

- [0027 - Severity binds to channel, and only critical rings a phone](../decision-log.md#adr-0027)
- [0028 - A watch never pages](../decision-log.md#adr-0028)
- [0029 - We recommend an action, and never execute it](../decision-log.md#adr-0029)
