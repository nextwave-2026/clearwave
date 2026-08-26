---
name: decision-capture
description: "Activate at decision-shaped moments during work: when rejecting a real alternative, cutting scope deliberately, accepting a constraint, taking a time-pressure trade-off, or changing design after a failure."
---

# Decision capture

Log only decisions that changed direction or scope. Routine implementation, naming, ordinary refactors, dependency picks with no real alternative, and facts already obvious in the diff do not earn entries.

When a qualifying moment occurs, ask the human for the rationale before recording it. Append this exact two-line shape to `DECISIONS.md`:

```text
- <ISO 8601 UTC>  <derek or andres>  <what changed, including the rejected alternative and the human's exact rationale>
  -> other side: <what they must now do differently>
```

The agent writes **what** was decided and **when**. The human supplies **why**, in their own words, quoted verbatim in the first line. Never invent, infer, or reconstruct the rationale. Ask for it, accept a fragment, and record that fragment verbatim. If the human has not supplied a rationale, do not fabricate an entry. The second line must state what the other side must now do differently. Append at the bottom; do not rewrite earlier entries. Commit this coordination update straight to `main`, not on a code branch or PR.
