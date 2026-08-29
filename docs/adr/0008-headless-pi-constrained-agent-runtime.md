# 0008 - Headless Pi is the constrained agent runtime

## Status

Accepted

## Context

The investigation needs tool orchestration and non-interactive output, while raw filesystem and shell access would violate the evidence boundary. `pi-coding-agent` is MIT licensed.

## Decision

Use headless `pi-coding-agent` in non-interactive print mode with JSON output. Disable built-in shell, file-read, edit, and write tools. The agent's only capabilities are gateway-backed evidence tools.

The documented fallback is a direct model-API tool-calling loop if Pi proves unreliable.

## Alternatives considered

- Direct API from the start - rejected because it reimplements tool orchestration already available.
- Interactive product harness - rejected because it is fragile under demo conditions.

## Consequences

The agent cannot access raw events, local files, or arbitrary commands. Adapter validation, timeout handling, and gateway citations remain mandatory. The fallback preserves the boundary while removing dependence on Pi reliability.
