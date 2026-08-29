# 0015 - Severity is bounded by money, not only weighted by it

## Status

Accepted

## Context

Severity is business priority and is owned by detection; diagnostic confidence is owned by investigation, and the two never collapse into one score. Priority is therefore a function of business impact: loss rate, blast radius, persistence and trajectory.

A weighted sum of those four has a defect that only shows up on cheap incidents. Persistence and trajectory can climb on their own, so an incident losing a trivial amount for a long time and slowly worsening accumulates enough score to reach a middling band. An incident losing $120 an hour for an hour is still a $120-an-hour incident, and the product baseline's ranking case requires it to sit below a high-volume incident losing thousands.

## Decision

Severity is a weighted sum of the four components, then capped by a loss-rate ladder: below a floor an incident cannot exceed `low`, below the next step it cannot exceed `medium`, and so on.

Money is therefore both a term and a ceiling. The loss-rate term is log-scaled between a floor and a cap, so a large loss decisively outranks a small one without outranking it by the raw ratio.

`severity_of()` takes no statistical argument at all. There is no parameter through which evidence strength could reach it.

## Alternatives considered

- A pure weighted sum - rejected: persistence and trajectory promote trivially cheap incidents, which is the defect this record exists to fix.
- Loss rate alone - rejected: it ignores blast radius and cannot distinguish a brief spike from a sustained bleed.
- Folding diagnostic confidence into the score - rejected outright: it is the collapse the product baseline forbids, and it would suppress exactly the case that matters most, where conversion has collapsed and the evidence cannot yet separate two causes.

## Consequences

A critical severity with low diagnostic confidence is valid and required output, because nothing about confidence can reach severity. The graded ranking behaviour - a large merchant's small percentage shift outranking a tiny cohort's dramatic one - falls out by construction rather than by tuning, and is covered by tests. Weights, the log floor and cap, and the ladder are all config under `CONFIG_VERSION`.
