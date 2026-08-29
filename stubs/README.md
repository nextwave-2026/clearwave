# Offline vertical slice

Run the complete fixture-backed path from the repository root:

```sh
python3 stubs/slice.py
# or
make slice
```

The command has five visible stages:

1. **Canonical events** loads representative normalized events from `stubs/fixtures/canonical_events.json`.
   This stands in for W1's simulated world and W2's normalization boundary; Raul replaces it.
2. **Incident record** emits the C3 detector result. This stands in for W2's deterministic detection,
   impact calculation, and prioritization; Andres replaces it.
3. **Evidence bundle** invokes the ten subprocess tools in `stubs/evidence/`. Each tool returns its
   fixture-backed answer with `query_id` and `as_of`. W2 replaces each tool independently with the
   real C2 query implementation.
4. **Investigation result** emits the C4 result using cited query IDs and no model call. This stands
   in for W3's investigation agent; Derek replaces it.
5. **Surface summary and escalation** renders the TAM-facing summary and severity channels. This
   stands in for W4's dashboard, notification, and phone escalation; Juank replaces it.

Run any evidence tool on its own with one JSON object on stdin, for example:

```sh
printf '%s\n' '{"cohort":{"merchant_id":"merchant-a"},"window":{"start":"2026-08-29T10:00:00Z","end":"2026-08-29T10:15:00Z"}}' \
  | python3 stubs/evidence/cohort_metrics.py
```

The stubs use only Python 3's standard library and local JSON fixtures. Replacing a stub must
preserve its documented contract; update `INTERFACES.md` and coordinate the change before changing
a contract.
