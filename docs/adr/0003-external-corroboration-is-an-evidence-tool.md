# 0003 - External corroboration is an evidence tool

## Status

Accepted

## Context

Provider health and public status can corroborate a diagnosis, but they have the same timeout, citation, and availability concerns as other evidence sources.

## Decision

External corroboration sits behind the same evidence gateway as every other tool. It is one evidence tool, not a separate component. External evidence may strengthen or weaken a hypothesis, but never overrides first-party observational evidence.

## Alternatives considered

- Dedicated corroboration service - rejected because a component whose only distinction is its data source adds no useful boundary.

## Consequences

Provider health inherits common query identity, citation, timeout, and failure handling. Its unavailability never fails an investigation. A healthy public status page cannot erase contradictory first-party observations.
