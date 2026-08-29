# 0018 - Native source shapes are normalised through a mapper registry

## Status

Accepted

## Context

Merchant simulators emit deliberately heterogeneous native shapes, and normalisation into the canonical model belongs to the detection plane. More than one shape already exists: the canonical shape used by the vertical slice, and an earlier envelope circulated before normalisation moved to this workstream.

Reshaping producers to suit the consumer would erase the heterogeneity that mirrors the real orchestrator problem, and would make every producer change a coordination event.

## Decision

A mapper is a pure function from one native record to a canonical one, held in a registry keyed by shape name. Adding a source is adding a mapper; nothing downstream changes.

A record's own `schema` field selects its mapper, because a source that declares itself is never guessed at. Otherwise the shape is inferred from the fields that actually differ between registered shapes. An unregistered shape is refused rather than coerced, and the refusal is dead-lettered with its reason.

Both currently known shapes are registered, so anyone who built to either is already correct.

## Alternatives considered

- Require every producer to emit the canonical shape - rejected: it removes the heterogeneity the scenario depends on and makes the consumer's convenience a producer constraint.
- Infer the shape from the record every time - rejected as the primary path: a declared schema is authoritative and inference is only the fallback.
- Coerce an unrecognised record on a best-effort basis - rejected: a quietly mis-mapped record produces a wrong count that cannot be detected later, which is worse than a visible rejection.

## Consequences

A producer can change shape without breaking consumers, provided the change is registered. The circulated envelope is kept verbatim as a test fixture, so if it ever stops normalising the test says so rather than the person who built to it discovering it during the build window. Minor-unit conversion uses the ISO 4217 exponent for the currency rather than a fixed two.
