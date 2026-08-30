# Scaling the detection plane

**What this document is.** Yuno processes roughly two million payments a day. A judge is
entitled to ask whether this survives that, and this is the answer to that question. It is
**not** a claim about what ships. Nothing described under "What we would change" is built, and
where a number is measured this document says so and says on what.

Written by `andres`, who owns L2 and L3.

## The short version

Ingest is not the wall. The cohort search is, and it is one specific loop.

## Ingest is comfortable, which is the counter-intuitive part

Two million payments a day is about **23 payments a second** averaged, and with retries the
attempt rate is somewhat higher - call it 30 attempts a second sustained, more at peak.

That is not a demanding write rate for the current store, and it is worth saying plainly because
the intuition runs the other way. `detector/store.py:write_batch` stages a whole batch inside the
caller's transaction and the consumer commits it, so the per-row cost is an `INSERT OR IGNORE`
and the fsync is amortised across the batch rather than paid per event.

**Measured on this machine** (WSL2, batches of 500, one commit per batch, through the real
`normalise` and `write_batch` path, no shortcuts): **50,000 attempts written in 2.00 seconds, or
about 25,000 accepted rows per second.** Against a requirement of ~30/s that is roughly three
orders of magnitude of headroom.

The honest caveats: this is a local file on one disk with no contention, the table grows and
index maintenance costs more as it does, and a single SQLite writer serialises - so this is a
statement about the write path's per-row cost, not a promise about a year of retained history.
But it does establish that ingest is not where this system falls over, and it means the
optimisation effort belongs elsewhere.

## The cohort search is the wall, and it is one loop

In `detector/detect.py`, inside the descent loop of `localise()`, localisation enumerates cohort
values like this:

```python
for dimension in (d for d in considered if d not in current):
    values = connection.execute(
        f"SELECT DISTINCT {dimension} AS v FROM attempt "
        f"WHERE {dimension} IS NOT NULL ORDER BY {dimension}"
    ).fetchall()
    ...
    for row in values:
        child = dict(current)
        child[dimension] = row["v"]
        evaluation = evaluate(connection, child, start, end)
        if evaluation["absolute_drop"] is None:
            continue
        if evaluation["observed"]["attempted_payments"] < config.N_PAYMENTS_MIN:
            continue
```

Two properties of that code are the whole cost of the system.

**First, the `SELECT DISTINCT` has no time bound.** It scans the entire retained `attempt`
history, not the detection window. A merchant that transacted once, six months ago, and has been
silent since is still returned, still turned into a child cohort, and still evaluated. The
enumeration is therefore sized by *total historical cardinality*, which only ever grows, rather
than by what is active now.

**Second, the volume floor is applied after `evaluate()`, not before.** `N_PAYMENTS_MIN` is
checked on the *result* of a full aggregate. Every dead cohort is fully measured against its
baseline and only then discarded. The work to reject a cohort is the same as the work to accept
one.

That runs once per dimension per depth level. `detector/schema.py:DIMENSIONS` declares six -
`merchant_id`, `provider`, `payment_method`, `card_network`, `country`, `issuing_bank` - and
`config.LOCALISE_MAX_DEPTH` is 3.

With three merchants and four banks this is invisible. With real cardinality it is everything.

### Measured

Same code, same 60,000 attempts, same detection window. Only the cardinality of the data
changes:

| Cardinality | Distinct values across the six dimensions | `localise()` wall time |
| --- | --- | --- |
| 3 merchants, 4 issuing banks (demo shape) | ~21 | **4.1 s** |
| 200 merchants, 500 issuing banks | ~714 | **113.2 s** |
| 2,000 merchants, 3,000 issuing banks | ~5,014 | **491.4 s** (8 min 11 s) |

The data volume is identical in every row. Only the number of distinct cohort values differs.
Cost tracks the cardinality of the *dimensions*, not the size of the *data* - which is the whole
point, and it is why a demo that feels instant tells you nothing about production.

Per cohort evaluation that is 197 ms, 159 ms and 98 ms respectively. The unit cost falls as
cardinality rises, because each cohort holds proportionally fewer rows to aggregate - so the
growth is not quite linear in distinct values, it is somewhat better than linear. That is the
generous reading and it does not rescue the result: **the third row takes eight minutes at depth
1 alone**, before depth 2 and depth 3 are considered at all, and a detection sweep is supposed
to complete inside a one-minute bucket.

Real Yuno cardinality - thousands of merchants, thousands of issuing banks, every country and
network they support - is larger than the third row.

## What we would change

**None of this is built.** These are the two changes we would make, in the order we would make
them.

### 1. Bound the enumeration before evaluating it

Apply the volume floor *before* enumerating rather than after evaluating: ask the store for the
cohort values that actually carry at least `config.N_PAYMENTS_MIN` payments inside the detection
window, instead of asking for every value that has ever existed and then measuring each one to
find out.

This is a change of a few lines and it does not change a single answer. Any cohort it removes
from the search is one that `evaluate()` would have measured and then discarded on the identical
floor. The search becomes bounded by cohorts *active in the window* rather than by *total
historical cardinality* - and the number of merchants transacting meaningfully in any given
five-minute window is a small fraction of the number that have ever transacted.

It is the first thing to do because it is cheap, it is provably answer-preserving, and it
converts a cost that grows forever with retained history into one that tracks current activity.

### 2. Pre-aggregate, so detection never scans raw rows

The larger fix, and a real piece of work rather than a patch: maintain per-bucket counters keyed
by cohort as events arrive - attempts, approvals, value, and the leading-indicator sums of ADR
0024 - so detection reads pre-aggregated rows instead of aggregating raw ones on every sweep.
Detection becomes a scan of a small counter table whose size is set by active cohorts times
buckets retained, and `evaluate()` stops being an aggregate at all.

The natural home for those counters at Yuno's volume is a columnar or time-series store rather
than SQLite. **That is not a decision this repository has taken.** `DECISIONS.md` records
relational SQLite as the persistence choice (`derek`, 2026-08-29T19:17Z) and records no successor;
a columnar store is a candidate named here, not a standing decision, and it would need recording
in `DECISIONS.md` before anyone built against it.

## How this compares to Payrails

Payrails published their approach to the same problem
(<https://www.payrails.com/blog/building-intelligent-anomaly-detection-at-payrails>), and it is
worth comparing honestly because they hit the same wall and turned the other way.

Their concrete architectural detail: "For the POC, we trained multiple models specifically for
each merchant and provider combination", which "resulted in hundreds of detection models running
in parallel". They then **deliberately declined** to segment more finely by country, payment
method or currency, giving two reasons: "Forecasting models struggled with the sheer data size
and complexity", and "More granular segments were too noisy, leading to too many false
positives". They call choosing the granularity "one of the most important technical decisions of
the project", and they are right that it is.

That is the same tractability problem, solved the opposite way. **They bought cheapness with
coverage; we bought coverage with cost.** Their search space is fixed at merchant x provider and
their per-segment cost is a trained model. Ours is every combination of six dimensions to depth
3, and our per-cohort cost is an aggregate - which is why our wall is a search loop and theirs
was model count.

Neither choice is obviously right, and the comparison is only useful if it runs both ways.

**Where they are ahead of us.** Their per-segment models learn seasonality. Ours do not learn
anything: `detector/config.py` states outright that the trailing-window baseline is a v0
placeholder, to be replaced by a seasonal hour-of-week baseline "once W1 provides replayable
backfill history". A flat trailing hour will misread a normal Monday-morning ramp in a way a
seasonal model will not. That is a real gap and it is ours, not theirs.

**Where we are ahead.** Our live run localised an incident to `{country: CO, merchant_id:
merchant-b, provider: adyen}` - a three-dimension cohort that includes exactly the country axis
they declined to segment on. A provider failing in one country and healthy elsewhere is a real
and common failure, and a merchant-x-provider model cannot express it; the incident either
dilutes below the threshold or fires against the whole provider. Our descent finds it because
nothing about any particular path is encoded, only the rule for descending.

**On the risk they named and we did not.** They backed away from fine-grained segments partly
because those segments were noisy and produced false positives. We did not back away, so we owe
a concrete answer for why. Ours is three guards, all of them named in config and all of them
load-bearing rather than decorative:

- `config.N_PAYMENTS_MIN` (30) - a cohort with too little traffic cannot qualify at all, which
  is the direct answer to a noisy small segment.
- `config.SHRINKAGE_PRIOR_PAYMENTS` (50) - a low-volume cohort borrows its parent's rate weighted
  by sample size, so an eight-payment cell cannot have a wild baseline to deviate from.
- `config.LOCALISE_MIN_SEPARATION` (0.10) - a dimension only enters the reported cohort when one
  of its values is materially worse than the next, which is what stops a provider-wide outage
  being reported as one arbitrary issuing bank inside it (ADR 0017).

Those are three specific mechanisms rather than a claim of not having the problem. Whether they
are *sufficient* at Yuno's cardinality is not something we have measured, because we have not run
at Yuno's cardinality - and Payrails' report is direct evidence that the risk is real at that
scale. If our guards prove insufficient, the levers are those three numbers, and they are config
under `CONFIG_VERSION`.

## Summary

- Ingest is not the constraint. Measured at ~25,000 rows/s against a ~30/s requirement.
- The constraint is cohort enumeration in `localise()`: an unbounded `SELECT DISTINCT` over all
  retained history, plus a volume floor applied after a full aggregate rather than before.
- Measured, same 60,000 rows: 4.1 s at demo cardinality, 113.2 s at 200 merchants and 500 banks,
  491.4 s at 2,000 and 3,000 - and that last is depth 1 only, against a one-minute sweep budget.
- The cheap fix is to bound the enumeration by the window's active cohorts before evaluating.
  Same answers, a fraction of the work.
- The real fix is per-bucket counters keyed by cohort, so detection reads pre-aggregated rows.
- Neither is built. This is the answer to a question about scale, not a description of what
  ships.
