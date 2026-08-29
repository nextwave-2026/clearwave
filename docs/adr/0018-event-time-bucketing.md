# 0018 - All detection arithmetic buckets on event time

## Status

Accepted

## Context

Detection aggregates into time buckets and compares a recent window against a baseline. Those buckets can be keyed on when an event was observed by the consumer, or on when it actually happened.

Arrival time is easier and is wrong. It makes the result depend on consumer lag, on partition interleaving and on the wall clock, which means the same recorded stream produces different incidents on a second run.

## Decision

Every bucket, window, baseline and onset is computed from the event's own `occurred_at`. Nothing in the detection path reads the wall clock.

A lateness watermark trails the highest event time seen by `LATENESS_GRACE_SECONDS`. A bucket is sealed once it falls entirely below the watermark, and detection reads only sealed buckets. An event arriving for a still-open bucket is counted normally; one arriving for a sealed bucket is counted as late rather than retro-fitted into a window that has already been judged.

## Alternatives considered

- Bucket on arrival time - rejected: results become a function of consumer lag and the run is not reproducible.
- Bucket on event time with no watermark - rejected: a bucket can be judged while events are still arriving for it, so a late arrival silently changes a conclusion already drawn.
- Reprocess sealed buckets when late events arrive - rejected for the build window: it makes an incident's history mutable, and the reliability cost outweighs the accuracy gain at our volumes.

## Consequences

Replaying a recorded stream reproduces byte-identical buckets, incidents and severity, which is how the detector is tested and how a scenario can be rerun against the evaluator. It also means an investigation can be replayed offline rather than waiting for a live incident to debug against. Every producer must emit an event timestamp on every record.
