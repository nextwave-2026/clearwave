# 0013 - Hand-rolled OpenAI loop is the constrained agent runtime

## Status

Accepted - supersedes ADR 0008.

## Context

L4 needs an agent that can call ten Python 3 evidence tools while enforcing a
bounded query budget, wall-clock timeout, contract validation, one retry, and a
visible degrade path. The earlier Pi decision introduced a runtime mismatch:
Pi's tool-registration bridge is TypeScript, while the evidence tools are Python
3 subprocesses. It therefore requires a TypeScript extension shelling out to
Python and a parser translating Pi session events into C4 on the demo hot path.
Pi is also designed to continue until a coding task is complete, contrary to
L4's requirement to stop on a bounded budget. The estimate is 6-8 hours for Pi
versus 3-4 hours for a hand-rolled loop, with a weaker bound for Pi.

## Decision

Use a hand-rolled Python loop over the OpenAI Responses API. The direct
model-API tool-calling loop, previously recorded as the fallback in ADR 0008,
is promoted to the primary runtime. It calls only the gateway-backed evidence
tools and owns the bounded loop, timeout, retry, JSON contract validation, and
deterministic degrade behaviour.

## Alternatives considered

- Headless Pi - rejected because its TypeScript bridge would shell out to the
  Python tools and translate session events, while its continue-until-complete
  design conflicts with L4's bounded investigation; it also takes longer for a
  weaker bound.
- A heavier agent framework - rejected because frameworks optimise for
  open-ended assistants, while L4 needs a countdown and a hard stop.
- Our own LLM client or JSON validator - rejected because the OpenAI client and
  pydantic genuinely earn their place for those responsibilities.

## Consequences

L4 gains a Python manifest with exactly two third-party dependencies: the
OpenAI client (Apache-2.0) and pydantic (MIT). Both are permissively licensed
and must be declared in the graded licence inventory. The loop, bound, retry,
and degrade become ours to test, which is the point: they can be unit-tested
with a fake client and no model. No second language runtime appears on the
demo path.
