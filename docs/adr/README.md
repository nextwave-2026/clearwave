# L4 Investigation ADRs

These records lock the design of the L4 Investigation layer before implementation. Each ADR uses the same readable format: **Status**, **Context**, **Decision**, **Alternatives considered**, and **Consequences**. Status is `Accepted` for every decision below.

L4 is the Investigation layer, owned by `derek` and corresponding to W3. The layer sequence is L1 merchant emission, L2 ingestion and normalisation, L3 deterministic detection, L4 investigation, and L5 surfaces and escalation.

## Records

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
