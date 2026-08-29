# 0002 - Diagnostic confidence belongs to Investigation

## Status

Accepted

## Context

Severity expresses business impact and priority. Diagnostic confidence expresses the strength of causal evidence. A severe incident can have weakly discriminating evidence.

## Decision

Investigation produces diagnostic confidence. Detection supplies no confidence. Severity and diagnostic confidence remain independent product concepts and scores.

Detection may supply confounding facts, but those facts are evidence, not confidence. A valid and expected state is therefore allowed to be critical priority with low diagnostic confidence.

## Alternatives considered

- One combined score - rejected because it would hide the distinction between urgency and causal certainty.
- Detection-supplied confidence - rejected because causal evidence is assessed by Investigation.

## Consequences

C3 carries severity and C4 carries diagnostic confidence. Downstream surfaces must render both independently. Low confidence never lowers a business priority that Detection established.
