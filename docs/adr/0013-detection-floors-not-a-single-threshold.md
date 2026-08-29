# 0013 - Detection qualifies an incident behind four floors

## Status

Accepted

## Context

A conversion drop is not by itself an incident. The challenge brief names both failure modes of the status quo: alerts that fire on everything and get ignored, and alerts that fire on nothing. A single threshold picks one of those failures. A percentage threshold alone fires constantly on small cohorts, where a handful of payments moves the rate double digits. A significance test alone fires on a large cohort whose conversion moved by a quarter of a point, because significance grows with sample size and stops meaning importance.

## Decision

A deviation becomes an incident only when all four floors hold together:

- a two-proportion z-test against the cohort's contextual baseline reaches `Z_MIN`, so the change is statistically real;
- the absolute drop reaches `ABS_DROP_MIN`, so it is operationally meaningful and not a significant quarter-point;
- the cohort has at least `N_PAYMENTS_MIN` payments, so a handful of transactions cannot raise an incident;
- the deviation is sustained across `SUSTAIN_BUCKETS` consecutive buckets, so a one-minute blip does not page anyone.

Every floor is reported alongside its result, so a near-miss can be explained rather than merely not happening. All four live in `detector/config.py` under a `CONFIG_VERSION` that travels with each incident.

## Alternatives considered

- A single percentage threshold - rejected: it fires constantly on low-volume cohorts and misses a large merchant's small, expensive shift.
- A significance test alone - rejected: z grows with sample size, so a high-volume cohort trips on an operationally irrelevant change.
- A learned or adaptive threshold - rejected: nothing to train on inside the build window, and an unexplainable threshold is worse than a crude defensible one when the technical defence is weighted as heavily as the demo.

## Consequences

Sensitivity is tuned by changing config, never by narrowing the cohort search space, which preserves general localisation as the defensible property. A quiet window produces no incident, and that silence is a graded behaviour rather than an absence of work. The floors are also why normal traffic can run in front of judges without the dashboard filling up.
