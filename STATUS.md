# Status

Append-only progress log. Newest entries at the bottom. Never edit or delete an existing entry.

This file is declared `merge=union` in `.gitattributes`, so both sides can append freely without
conflicts. **Every entry names its host**, because there is no shared supervisor between the two sides.

Write here whenever you finish something the other side should know about. A decision the other side
must build around goes in `DECISIONS.md` instead; the current shape of a boundary goes in
`INTERFACES.md`.

Format: one line per entry.

```
- <ISO 8601 UTC timestamp>  <host>  <what happened, and what it means for the other side>
```

## Log

- 2026-08-24T01:31Z  hostA  Preparation kit repository created and seeded: nextwave-2026/nextwave-kit, public, its own independent history. Declared pre-existing IP.
- 2026-08-24T01:45Z  hostA  Kit complete and merged to its main. Contains the Makefile target contract, CI workflow, offline licence inventory generator, coordination files, and the three deliverable templates. No application scaffold and no language commitment, by design - the stack depends on the challenge.
- 2026-08-24T01:47Z  hostA  Kit verified independently rather than on trust: `make licences` proven against both Node and Python manifests, idempotent, hand-written policy survives regeneration, no network access needed. Union merge proven with simultaneous appends from two branches - both survived, no conflict.
- 2026-08-24T01:57Z  hostA  THIS entry repository created: nextwave-2026/clearwave, public. Name is deliberately product-like and domain-agnostic; do not rename it to match the challenge.
- 2026-08-24T01:59Z  hostA  Kit infrastructure COPIED into this repository, never forked. Verified no common ancestor between the two histories, which is what keeps the kit pre-existing IP. Provenance declared in the first commit message and the README "Pre-existing components" section - do not remove either.
- 2026-08-24T02:00Z  hostA  Continuous integration verified GREEN on the real service, not assumed: all five named checks passed on the first commit. Templates placed where they will be used - README.md and ARCHITECTURE.md at the root, docs/pitch.md.
- 2026-08-24T02:05Z  hostA  Orientation written to AGENTS.md and this log started. hostB: read AGENTS.md first, it carries the Makefile contract, the licence ban, the history-separation rule, and the merge behaviour of each coordination file.
- 2026-08-24T02:05Z  hostA  PENDING on hostB's side: hostB's developer has two unaccepted GitHub invitations - organisation owner, and admin on this repository. Neither side can push from hostB until those are accepted. hostB: confirm and report here.
- 2026-08-24T02:05Z  hostA  NEXT for hostB: prove an end-to-end run on hostB's own setup - dispatch a worker, get a green pull request, report the round trip here. hostA's side is already proven. This is the last unverified part of the arrangement.
