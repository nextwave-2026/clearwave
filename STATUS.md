# Status

Append-only progress log. Newest entries at the bottom. Never edit or delete an existing entry.

This file is declared `merge=union` in `.gitattributes`, so both sides can append freely without
conflicts. **Every entry names who wrote it** - use your GitHub username (`derek` or `andres`), because
there is no shared supervisor between the two sides and an unattributed line is close to useless.

**Commit this file straight to `main`** - no branch, no pull request. That is the point of it: a
progress line the other side needs now is useless sitting in review. If your push is rejected because
they pushed first, run `git pull --no-rebase && git push`; union merge keeps both entries.

Write here whenever you finish something the other side should know about. A decision the other side
must build around goes in `DECISIONS.md` instead; the current shape of a boundary goes in
`INTERFACES.md`.

Format: one line per entry.

```
- <ISO 8601 UTC timestamp>  <who>  <what happened, and what it means for the other side>
```

## Log

- 2026-08-24T01:31Z  derek  Preparation kit repository created and seeded: nextwave-2026/nextwave-kit, public, its own independent history. Declared pre-existing IP.
- 2026-08-24T01:45Z  derek  Kit complete and merged to its main. Contains the Makefile target contract, CI workflow, offline licence inventory generator, coordination files, and the three deliverable templates. No application scaffold and no language commitment, by design - the stack depends on the challenge.
- 2026-08-24T01:47Z  derek  Kit verified independently rather than on trust: `make licences` proven against both Node and Python manifests, idempotent, hand-written policy survives regeneration, no network access needed. Union merge proven with simultaneous appends from two branches - both survived, no conflict.
- 2026-08-24T01:57Z  derek  THIS entry repository created: nextwave-2026/clearwave, public. Name is deliberately product-like and domain-agnostic; do not rename it to match the challenge.
- 2026-08-24T01:59Z  derek  Kit infrastructure COPIED into this repository, never forked. Verified no common ancestor between the two histories, which is what keeps the kit pre-existing IP. Provenance declared in the first commit message and the README "Pre-existing components" section - do not remove either.
- 2026-08-24T02:00Z  derek  Continuous integration verified GREEN on the real service, not assumed: all five named checks passed on the first commit. Templates placed where they will be used - README.md and ARCHITECTURE.md at the root, docs/pitch.md.
- 2026-08-24T02:05Z  derek  Orientation written to AGENTS.md and this log started. andres: read AGENTS.md first - it carries the Makefile target contract, the licence prohibition, the history-separation rule that keeps the kit ours, and the merge behaviour of each coordination file.
- 2026-08-24T02:05Z  derek  PENDING: andres has two unaccepted GitHub invitations - owner of the nextwave-2026 organisation, and admin on this repository. Pushing from that side is blocked until both are accepted. andres: accept them and confirm here.
- 2026-08-24T02:05Z  derek  NEXT for andres: prove one end-to-end run on that side - dispatch a worker, land a green pull request, and report the round trip and anything that surprised you here. derek's side is already proven. This is the last unverified part of the whole arrangement.
- 2026-08-24T02:20Z  derek  Coordination files now commit STRAIGHT TO MAIN - no branch, no pull request. A status line the other side needs now is useless sitting in review. Safe because STATUS.md and DECISIONS.md are union-merged: verified that two sides committing to main from a stale base recover with a plain `git pull --no-rebase && git push`, both entries surviving. Code still goes through a pull request. See the Working conventions section of AGENTS.md.
