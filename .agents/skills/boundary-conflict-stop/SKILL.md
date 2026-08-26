---
name: boundary-conflict-stop
description: "Activate immediately on any merge conflict in `INTERFACES.md`, or when the other operator's declared interface contradicts assumptions in local code."
---

# Boundary conflict means stop

Stop immediately. A boundary conflict is information, not a merge obstacle.

- Do not auto-resolve `INTERFACES.md`.
- Do not choose the newer, longer, or local version.
- Do not continue implementation or silently edit around the disagreement.

Preserve and surface both versions. Explain concretely:

- what side A promises, including owner and request/response/event shape;
- what side B promises; and
- which local code assumptions break under each version.

For a Git conflict, include the conflict's two sides (and the relevant local code). For a semantic contradiction, quote the declared interface and the assumption that disagrees. Report this to the humans and wait for their reconciliation. If a proposal must be recorded, commit that coordination-file change straight to `main`, not on a code branch or PR. This skill never decides which side wins.
