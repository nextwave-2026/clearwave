# ClearWave evaluator

`score.py` compares a completed diagnosis with one quarantined C6 hidden-truth record. It produces
an explainable JSON object with cohort localisation, uncertainty handling, relative-priority
checks, and an overall verdict.

## Run one score

From the repository root:

```sh
python3 evaluator/score.py diagnosis.json hidden-truth.json
```

The diagnosis file may be the C4 result directly when its incident carries
`affected_cohort`, or an evaluator envelope that keeps runtime contracts separate:

```json
{
  "diagnosed_cohort": {
    "provider": "provider-p2",
    "issuing_bank": "bank-x"
  },
  "priority_rank": 1,
  "investigation_result": {
    "incident_id": "inc-1",
    "leading_hypothesis": {
      "statement": "Provider P2 is the leading hypothesis."
    },
    "competing_explanations": [
      {"explanation": "Bank X over-decline remains possible."}
    ],
    "missing_evidence": [
      {"request": "Compare P2 through another issuer or Bank X through another provider."}
    ],
    "diagnostic_confidence": "medium"
  }
}
```

`diagnosed_cohort` and `priority_rank` are evaluator-envelope fields. They are not additions to
C4. The evaluator also accepts `localization`, `localisation`, `cohort`, or an incident
`affected_cohort` as a convenience for diagnosis fixtures.

The CLI prints structured JSON and returns the score process status, not the diagnosis verdict.
A diagnosis can therefore be inspected even when its verdict is `fail`.

## Score meanings

### Cohort localisation

The evaluator compares exact dimension-value pairs in the diagnosed cohort with
`injected.affected_cohort` in C6:

- `precision` is matched pairs divided by diagnosed pairs. Extra dimensions or wrong values lower
  precision.
- `recall` is matched pairs divided by injected pairs. Omitted dimensions or wrong values lower
  recall.
- `matched_dimensions`, `missing_dimensions`, and `spurious_dimensions` name the dimensions behind
  the result. A wrong value in an otherwise present dimension appears in both missing and spurious
  lists, and `mismatched_dimensions` shows expected versus diagnosed values.
- `passed` requires both precision and recall to be 1.0 and both cohort objects to be present.

The evaluator scores against the direct injected slice, not a side-effect cohort in
`observed.affected_cohorts`.

### Uncertainty handling

For a truth record with `evaluation.confounded: true`, all of the following are required:

1. a leading hypothesis;
2. a named competing explanation;
3. a concrete missing-evidence request; and
4. `diagnostic_confidence` of `low` or `medium`, never `high`.

A confident single-cause answer fails this component even when its cause happens to match the
injected cause. This is intentional: a diagnosis that could not know the answer must not receive
credit for theatrical certainty. Non-confounded scenarios do not require hedging and report this
component as `not_applicable`.

### Severity ordering

Scenario truth can declare `evaluation.priority_relations`, for example that
`high-impact-small-percentage` `outranks` `dramatic-low-volume-anomaly`. Ordering is evaluated
with `score_rankings`, not by comparing an absolute severity label. A lower `priority_rank` means
higher business priority; only the relative order matters.

```python
from evaluator.score import score_rankings

result = score_rankings([
    {"diagnosis": high_impact_diagnosis, "hidden_truth": high_impact_truth},
    {"diagnosis": low_volume_diagnosis, "hidden_truth": low_volume_truth},
])
assert result["verdict"] == "pass"
```

`score_rankings` returns each per-scenario score plus every ordering check. A missing peer, missing
rank, reversed order, or unsupported relation is explicitly reported and fails the ordering
component. The check passes for ranks 10 and 20 just as it does for ranks 1 and 2, proving that it
checks ordering rather than an absolute value.

## Test

```sh
python3 evaluator/test_score.py
make evaluate
```

The tests use only Python's standard-library `unittest`. They cover exact fixture-backed
localisation, broad and spurious cohorts, confounded certainty failure, correctly hedged
confounded diagnosis, and high-impact ordering above a dramatic low-volume anomaly.

## Quarantine

The evaluator is outside the diagnostic path. Detection and Investigation do not import this
package, query C6, receive hidden truth, or receive scenario identifiers. The evaluator opens a
hidden-truth record only after a diagnosis has been produced and cannot feed its score back into
Detection or Investigation. A system that can see its answer has not demonstrated anything.
