# 0001 - Investigation assembles its own evidence bundle

## Status

Accepted

## Context

Detection produces a deterministic drill-down path, but selecting which evidence bears on a cause is investigative judgement. Detection cannot know what the agent will need without coupling the two layers.

## Decision

L4 assembles its own evidence bundle. L3 hands over the drill-down path it already computed as a deterministic fact, but does not pre-compute an evidence bundle for L4.

## Alternatives considered

- Detection-built bundle - rejected because it couples layers and puts a judgement in the wrong owner's hands.
- Agent queries with no opening set - rejected because the agent would spend its bounded budget rediscovering what Detection already knows.

## Consequences

L3 remains responsible for what happened and its deterministic localisation. L4 starts from useful context while retaining responsibility for evidence selection and causal reasoning. Changes to investigative reasoning stay within L4 rather than becoming Detection changes.
