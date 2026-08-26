---
name: claim-before-build
description: "Activate at the start of any new unit of work, before writing code, and whenever resuming after a break. Pull the latest coordination state before deciding whether to build."
---

# Claim before build

Do this before creating or editing implementation files:

1. Pull the latest `main` with fast-forward-only (`git pull --ff-only origin main`). If the branch cannot fast-forward, fetch and inspect `origin/main` instead; do not overwrite local work.
2. Read `STATUS.md`, `DECISIONS.md`, `INTERFACES.md`, and the other operator's commits since the last sync.
3. Compare the proposed unit of work with those records. Decide whether it is already claimed, clearly overlaps work in flight, or crosses a declared interface boundary.

- If it is claimed or clearly overlaps, stop. Name the existing claim or overlapping work and do not build a second version.
- If it crosses an interface, stop and write the proposal to `INTERFACES.md`. Commit coordination-file changes straight to `main`, never in a code branch or PR. Do not proceed unilaterally; the boundary owner and both implementations must align first.
- If it is clear, append a dated claim to `STATUS.md` before starting work, using its one-line format: who, what you are starting, and what it means for the other side. Do not add a separate branch field. Commit this coordination update straight to `main`, separately from code.

If the wording is ambiguous, do not issue a confident verdict. Show the concrete records and ask the human to clarify. Never quietly duplicate work.
