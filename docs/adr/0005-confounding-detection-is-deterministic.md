# 0005 - Confounding detection is deterministic

## Status

Accepted

## Context

Whether two dimensions are structurally inseparable in an observation window is established by a cross-tabulation. It is a property of the observed data, not a model's judgement.

## Decision

Detection computes confounding deterministically and supplies the result as a fact. Investigation interprets and explains the confounding, but never determines whether it exists.

## Alternatives considered

- Ask the agent to notice confounding - rejected because honest uncertainty would depend on model attention and could be missed.

## Consequences

The criterion, cross-tabulation, and result are auditable evidence. Investigation can say why provider and issuer cannot be separated without inventing a structural fact or presenting a model guess as certainty.
