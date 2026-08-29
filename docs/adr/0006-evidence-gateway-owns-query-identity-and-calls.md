# 0006 - Evidence gateway owns query identity and calls

## Status

Accepted

## Context

Citations must refer to evidence that actually ran. Trusting an agent-written query identifier would permit fabricated or unexecuted evidence to appear authoritative.

## Decision

The evidence gateway is the sole caller of evidence tools and assigns every query id. Every call and response is recorded to the evidence trail as it happens.

## Alternatives considered

- Let the agent call tools directly - rejected because the caller could cite an unexecuted query and trail completeness would depend on cooperation.

## Consequences

Citation is structural: the agent can only cite gateway-issued identifiers. Tool parameters, responses, timestamps, durations, and outcomes are inspectable, including failures. The gateway remains a transport boundary and holds no domain judgements.
