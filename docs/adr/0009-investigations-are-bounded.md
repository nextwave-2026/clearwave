# 0009 - Investigations are bounded

## Status

Accepted

## Context

Agent runtime turn limits are not sufficient to bound wall-clock behaviour. Unbounded loops could consume the demo's latency and model budget.

## Decision

Every investigation has a fixed opening evidence set, a wall-clock timeout, and a maximum of six further queries. Six is a starting value chosen to bound latency and cost and may be tuned with evidence.

## Alternatives considered

- Allow an unbounded evidence loop - rejected because unbounded agent loops are forbidden by the baseline.
- Omit the opening set and use only a query cap - rejected because the agent would waste its budget finding baseline facts.

## Consequences

The runner enforces wall-clock timeout and the gateway enforces query budget. An investigation always reaches a result or a visible degraded outcome. The starting budget is operationally tunable, not a hidden product guarantee.
