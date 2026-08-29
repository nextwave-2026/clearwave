# Integration guide for workstream agents

Read this guide before changing anything under `stubs/` or implementing a workstream. Treat it as a binding integration contract, not optional background.

## What the stubs mean

The files under `stubs/` are an executable definition of the seams between the four workstreams. They are not example code, throwaway scaffolding, or work to delete. The vertical slice makes every boundary runnable before the real components are complete.

## Binding rule

Replace your own layer's stub in place, behind the same contract. Keep its invocation boundary and response boundary stable. Never change another layer's stub. Never change a contract to make your implementation easier.

The ownership mapping is:

- Stage 1, canonical events: W1 (`raul`).
- Stages 2 and 3, the incident record and evidence tools: W2 (`andres`).
- Stage 4, the investigation result: W3 (`derek`).
- Stage 5, the surface and escalation summary: W4 (`juank`).

The integration invariant is absolute: `python3 stubs/slice.py` must exit 0 and print all five stage markers at every commit, regardless of how much real implementation exists. This is the integration smoke test for the whole system. Run it before you push.

## Replacing a stub

1. Identify the stage and confirm that it is your workstream's stage.
2. Read the owning contract and the consumers in `docs/contracts/` before editing.
3. Replace the stub in place. Keep the same input and output shape and keep every field named by the contract.
4. For every evidence-tool response, preserve a non-empty `query_id` and `as_of`. The citation trail depends on both fields.
5. Run `python3 stubs/slice.py` and the relevant focused checks before pushing.

If the real implementation genuinely needs a contract change, stop. Update `INTERFACES.md`, record the decision in `DECISIONS.md`, and announce it in `STATUS.md` so the other three workstreams see it. Never change a shape unilaterally. A silent contract change is the single most expensive mistake available in this repository.

## Do not over-help

- Do not delete the stubs.
- Do not delete or edit fixtures for a layer that is not yours.
- Do not "improve" a contract shape.
- Do not add fields another layer must understand without recording the change.
- Do not bypass a failing guard by weakening it.

Keep the old seam runnable while the real implementation grows. If a guard fails, fix the implementation or coordinate a contract change; do not make the guard less strict.

## Worked example: one evidence tool

Replace `cohort_metrics` in place with a real SQLite-backed implementation, but leave its command and JSON contract unchanged. The caller does not know or care whether the response came from a fixture or SQLite.

Before, the fixture-backed stub is invoked like this:

```sh
printf '%s\n' '{"cohort":{"merchant_id":"merchant-a"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}' \
  | python3 stubs/evidence/cohort_metrics.py
```

It returns this shape (the exact `query_id` is derived from the tool and input):

```json
{
  "query_id": "q_cohort_metrics_<16 hex characters>",
  "as_of": "2026-08-29T10:15:00Z",
  "cohort": {},
  "window": {},
  "payment_metrics": {"attempted_payments": 1000, "approved_payments": 640, "approval_conversion": 0.64},
  "attempt_metrics": {"attempts": 1350, "approved_attempts": 640, "approval_conversion": 0.4740740741, "failed_attempts": 710},
  "volume": {"attempted": {"amount": 100000.0, "currency": "USD"}, "approved": {"amount": 64000.0, "currency": "USD"}},
  "decline_mix": [],
  "baseline": {}
}
```

After replacing only that tool's internals with SQLite reads, invoke the exact same command. It must still return the same top-level and nested contract shape:

```json
{
  "query_id": "q_cohort_metrics_<16 hex characters>",
  "as_of": "2026-08-29T10:15:00Z",
  "cohort": {},
  "window": {},
  "payment_metrics": {"attempted_payments": 1000, "approved_payments": 640, "approval_conversion": 0.64},
  "attempt_metrics": {"attempts": 1350, "approved_attempts": 640, "approval_conversion": 0.4740740741, "failed_attempts": 710},
  "volume": {"attempted": {"amount": 100000.0, "currency": "USD"}, "approved": {"amount": 64000.0, "currency": "USD"}},
  "decline_mix": [],
  "baseline": {}
}
```

The real implementation may return different measured values and a different query identifier when its data changes, but it must retain the contract fields, and `query_id` must identify the exact `{tool, input}` call. The slice must remain green throughout the replacement.
