# 0022 - OpenAI structured outputs fit bounded investigation

## Status

Accepted - 2026-08-29

## Context

Detection has already found the incident, localised the affected cohort, measured the change,
priced the business impact, and packaged the evidence before a model is called. L4 does not ask a
model to discover the task or compute those facts. Its narrower job is to investigate, correlate,
explain uncertainty, and recommend operator actions over evidence it did not gather and cannot
fabricate. This boundary is specified in [`docs/l4-investigation-prd.md`](../l4-investigation-prd.md)
and implemented by the C3 input and C4 output contracts.

This is the opposite shape from a general-purpose coding-agent harness. A coding agent is designed
for open-ended work in which it discovers tools and decides what to do next until the task is
complete. L4 must stop when its budgets expire, call only eleven published evidence tools, and
return one auditable C4 object even when model work fails.

[ADR 0008](0008-headless-pi-constrained-agent-runtime.md) originally selected headless Pi, with a
direct model API as fallback. [ADR 0013](0013-hand-rolled-openai-loop-agent-runtime.md) superseded
that choice and promoted a hand-rolled Python loop over the OpenAI Responses API. The reasons
recorded at the time in [`DECISIONS.md`](../../DECISIONS.md) at `2026-08-29T19:54Z` remain true: Pi
required a TypeScript tool bridge around Python subprocesses, and its continue-until-complete design
fought the bounded investigation. This record explains why the implemented direct loop is the
architectural fit, not merely the faster implementation.

## Decision

Use the OpenAI Responses API directly for model calls and Pydantic for the C4 contract. Do not put a
general-purpose coding-agent harness on the investigation runtime path.

The direct loop makes the product bounds explicit in
[`investigation/agent.py`](../../investigation/agent.py): a fixed seven-query opening bundle, at
most six model/tool-gathering turns, at most six further evidence queries, a 300-second default
wall-clock deadline, per-call deadlines, one structured final call, one validation retry, and then
a visible `agent_unavailable` result. The model receives no shell, filesystem, raw-event, evaluator,
or arbitrary tool access. Its only tool calls go through the allowlist exposed by the evidence
gateway.

[`investigation/contracts.py`](../../investigation/contracts.py) defines C4 as strict Pydantic
models with extra fields forbidden and closed values for outcomes, diagnostic confidence, and tool
names. The final OpenAI call uses that model's JSON Schema as a strict structured-output format.
Pydantic then validates the returned object, while the adapter additionally rejects a mismatched
incident id, forbidden severity, unsupported confidence in confounded cases, claims without cited
evidence, and citations that do not resolve to executed calls.

[`investigation/gateway.py`](../../investigation/gateway.py) is the sole caller of C2. It assigns a
stable query id to every request and records the tool, parameters, response, timestamp, duration,
outcome, and whether the call executed. The adapter verifies every model citation against this
trail. The model can interpret measured evidence, but it cannot make an invented query id become a
measurement.

This gives the following properties:

- **Reliability:** a small, testable loop has fewer runtime seams than a harness process, a
  TypeScript extension, Python subprocesses, and session-event parsing. Timeouts, invalid output,
  and API failure all converge on the same visible degraded C4 shape rather than losing the
  incident. The direct API is still an external dependency and can fail; the deterministic C3
  incident, impact, localisation, and evidence remain available when it does.
- **Structured contracts:** strict OpenAI JSON Schema generation constrains the model response,
  Pydantic performs typed validation, and product-specific validation checks evidence provenance.
  Downstream surfaces receive C4 rather than a harness transcript that must be interpreted after
  the fact.
- **Controllability:** turn, query, tool, output-token, and wall-clock bounds are owned by our code.
  A harness-level instruction to stop would be advisory; gateway budget enforcement and adapter
  deadlines are executable policy.
- **Latency:** the direct path removes harness startup, bridge, and event-translation overhead. In
  one real maximum-query live run, the seven opening calls plus six further calls produced 13 trail
  entries in about 55 seconds and cost roughly USD 0.02. That is an observed run, not a latency or
  price guarantee, but it demonstrates a judge-visible result inside the current bound. Model
  inference remains the dominant and variable cost, so the choice does not make latency
  deterministic.
- **Implementation complexity:** the runtime remains Python and uses the existing Python evidence
  subprocess boundary. It has two focused third-party dependencies, OpenAI and Pydantic, instead of
  introducing a second language bridge and a harness-session adapter. We accept responsibility for
  the loop, retry, and degradation logic because those are product policy and are covered by fake
  client tests in [`tests/test_agent_loop.py`](../../tests/test_agent_loop.py).
- **Observability:** the authoritative record is the gateway trail, not model prose or framework
  telemetry. Every cited query id can be checked against the exact executed request and response,
  including failures and timing. A coding harness could provide a richer generic session trace,
  but that trace would not by itself prove that a cited business measurement ran.
- **Demo value:** the direct loop still demonstrates genuine model-directed evidence selection and
  reasoning, while the stable result and inspectable trail let a judge challenge any claim. The
  bounded failure path is also demonstrable. A harness might look more autonomous, but apparent
  autonomy is less valuable here than showing that the model cannot escape or counterfeit the
  evidence boundary.

## Alternatives considered

- **Headless Pi or another general-purpose coding-agent harness** - better when the objective is
  open-ended exploration: discovering unfamiliar tools, reading and modifying a repository,
  iterating until a loosely specified task is complete, or carrying out multi-step remediation.
  It would also be stronger if L4 were expected to discover new integrations at runtime or execute
  a repair and test whether it worked. Those are real advantages, but they are deliberately outside
  this product. Clearwave diagnoses and recommends; it does not let the model remediate production.
  On this bounded path, a harness adds a TypeScript-to-Python tool bridge, session lifecycle, and
  event translation while making the hard stop less direct.
- **Unstructured OpenAI output followed by JSON extraction** - simpler at the API boundary, but
  weaker for reliability and downstream contracts. It permits shape drift and spends the retry on
  errors the provider can reject during generation. Post-validation remains necessary either way,
  because a schema-valid citation can still refer to a query that never ran.
- **A heavier agent framework** - could supply reusable orchestration, tracing, memory, and tool
  discovery. Those features become valuable if the investigation expands into long-lived,
  cross-incident research. Today they duplicate a six-turn loop and risk turning our product bounds
  into framework configuration rather than code we directly test.
- **No model** - maximally deterministic and cheap, but rules cannot credibly explain novel
  correlations, distinguish supported from competing explanations across varied evidence, or write
  useful missing-evidence and next-action guidance without becoming scenario-specific. Detection
  should remain deterministic; investigation benefits from bounded model interpretation.

## Consequences

The investigation runtime is intentionally less general than a coding agent. It cannot discover a
new tool, inspect arbitrary files, or perform remediation. Adding either capability requires a new
boundary and a new decision rather than quietly widening this loop.

Strict structured outputs impose a real schema-design cost. OpenAI strict mode requires every
property at every nesting level to appear in `required`, even when Pydantic expresses an empty-list
or scalar default. `_strict_schema` in `investigation/agent.py` recursively rewrites the generated
schema, and the regression test asserts that every property is required. This constraint bit the
implementation: fields that are conceptually optional in content must still be present, usually as
an empty collection or explicit outcome, and defaults cannot simply be omitted from model output.
We accept that awkwardness for a stable C4 envelope, but it is a provider constraint, not an ideal
schema rule.

The team owns and tests the bounded loop, deadline handling, citation checks, one retry, and degrade
path. OpenAI price and latency remain variable, so the observed 55-second, roughly two-cent run is a
planning datum rather than a promise. Every result remains reviewable through its persisted C4
object and gateway evidence trail.

ADR 0013 remains the runtime decision and ADR 0008 remains superseded. This record builds on ADR
0013 by documenting the architectural trade-off and the conditions under which a coding-agent
harness would become the better choice.
