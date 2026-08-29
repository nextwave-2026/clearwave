# 0011 - One versioned run per incident

## Status

Accepted

## Context

Incidents evolve, but automatic re-investigation multiplies model cost, latency, and operator noise during the v1 demo.

## Decision

V1 runs one investigation per incident. Results carry a version, and operators may manually re-run an investigation.

It remains an open question whether automatic re-investigation should trigger on a severity-band change or material blast-radius growth.

## Alternatives considered

- Automatic re-investigation on every incident update - rejected because it multiplies cost, latency, and noise.
- Unversioned replacement results - rejected because operators need to know which run produced the visible assessment.

## Consequences

The runner has a simple idempotent v1 lifecycle and surfaces can identify a result version. Manual re-runs provide control while the automatic trigger policy remains explicitly unresolved.
