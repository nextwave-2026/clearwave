# 0010 - Every investigation emits a result

## Status

Accepted

## Context

An investigation failure must not make a detected incident disappear. Operators still need deterministic localisation, impact, and evidence when narrative generation is unavailable.

## Decision

Every run emits one of four outcomes: `diagnosed`, `ambiguous`, `insufficient_evidence`, or `agent_unavailable`. The incident is never dropped because investigation failed.

On `agent_unavailable`, deterministic incident facts, localisation, financial impact, and evidence still render. The narrative is explicitly marked unavailable.

## Alternatives considered

- Drop failed investigations - rejected because it hides the operational incident.
- Treat unavailable narrative as success - rejected because it would conceal degraded diagnosis.

## Consequences

Surfaces can render one stable result shape for success and degradation. Persistence records the outcome and preserves the trail, making failure visible and restart-safe rather than silent.
