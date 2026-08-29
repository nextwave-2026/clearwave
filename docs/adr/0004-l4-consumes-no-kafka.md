# 0004 - L4 consumes no Kafka

## Status

Accepted

## Context

Investigation needs the durable incident record and evidence-query surface, not the raw event stream. A broker consumer would introduce offsets and restart handling without improving the demo path.

## Decision

L4 reads incidents from the relational SQLite store and writes investigation results there. It has no Kafka consumer group, stores no offsets, and never exposes raw events to the agent.

## Alternatives considered

- Consume an incidents topic - rejected because it adds broker dependency and offset handling for no benefit at demo scale.

## Consequences

The runner polls persisted lifecycle state and can restart without replay coordination. Evidence remains the only route to measured facts, and the agent cannot inspect raw events regardless of runtime failure or prompt behaviour.
