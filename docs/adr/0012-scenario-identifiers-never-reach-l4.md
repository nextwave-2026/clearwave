# 0012 - Scenario identifiers never reach L4

## Status

Accepted

## Context

The demo must show generality rather than a scripted answer. Hidden scenario configuration belongs to the simulator and evaluator, not the diagnostic path.

## Decision

The same L4 code path serves every scenario. No scenario identifier may enter the layer, no L4 component may branch on one, and the agent is never told which scenario is running.

## Alternatives considered

- Pass a scenario id for prompt tuning - rejected because it would script the result and leak hidden truth.
- Give the agent the scenario catalogue - rejected because evaluation would no longer test generalisation.

## Consequences

Provider degradation, confounding, and high-impact small-percentage changes produce different evidence-backed outputs through the same path. The evaluator remains outside diagnosis, and no diagnosis is hardcoded.
