# Interfaces

Edit this file in place to record the current state of each interface boundary. Do not append historical versions. This file is intentionally not configured with the union merge driver: union would keep both the old and new value of an interface, leaving contradictory claims in one file. A merge conflict here is a genuine signal that both sides are editing the same boundary and should stop to reconcile it.

**Commit this file straight to `main`** - no branch, no pull request. Unlike the append-only logs this
file is NOT union-merged, so it can genuinely conflict. A conflict here means both sides are changing
the same boundary at once: agree on the shape, then commit the agreed version.

## Example boundary - delete before use

- **Boundary name:** Example boundary
- **Owner:** Example side
- **Current shape:** Replace this description with the current request, response, or event shape.
- **Last changed:** YYYY-MM-DDTHH:MMZ
