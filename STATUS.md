# Status

Append-only progress log. Newest entries at the bottom. Never edit or delete an existing entry.

This file is declared `merge=union` in `.gitattributes`, so all four contributors can append freely without
conflicts. **Every entry names who wrote it** - use your GitHub username (`derek`, `andres`, `juank`, or
`raul`), because there is no shared supervisor across the four independent machines and an unattributed
line is close to useless.

**Commit this file straight to `main`** - no branch, no pull request. That is the point of it: a
progress line everyone else needs now is useless sitting in review. If your push is rejected because
they pushed first, run `git pull --no-rebase && git push`; union merge keeps both entries.

Write here whenever you finish something everyone else should know about. A decision everyone else
must build around goes in `DECISIONS.md` instead; the current shape of a boundary goes in
`INTERFACES.md`.

Format: one line per entry.

```
- <ISO 8601 UTC timestamp>  <who>  <what happened, and what it means for everyone else>
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
- 2026-08-29T16:52Z  derek  Challenges published and Challenge 02 (Control Tower) chosen. All work targets that objective; full brief is `docs/challenge.md`.
- 2026-08-29T16:52Z  derek  Team grew to four: `juank` (GitHub `juank115`) and `raul` (GitHub `raulhiguerac`) joined `derek` and `andres`. Use those four handles for attribution.
- 2026-08-29T16:52Z  derek  Repository documentation brought in line with the chosen challenge (`docs/challenge.md`, `README.md`, `AGENTS.md`). Read those before building.
- 2026-08-29T16:52Z  derek  Architecture and stack are still undecided. Next thing that needs a human call. Do not start building components before that decision lands.
- 2026-08-29T18:09Z  derek  baseline PRD published at \; ownership boundaries and contract owners are documented in \, and PR #4 is open: https://github.com/nextwave-2026/clearwave/pull/4
- 2026-08-29T18:09Z  derek  stack decision and person-to-workstream assignment are now the two blockers to starting parallel implementation; the contracts and integration gate are ready
- 2026-08-29T18:09Z  derek  baseline PRD published at `docs/prd.md`; ownership boundaries and contract owners are documented in `docs/ownership.md`, and PR #4 is open: https://github.com/nextwave-2026/clearwave/pull/4
- 2026-08-29T18:09Z  derek  done: PR https://github.com/nextwave-2026/clearwave/pull/4
- 2026-08-29T18:11Z  derek  the STATUS.md entry timestamped 2026-08-29T18:09Z whose paths render as bare backslashes was corrupted by a tooling error and is void and superseded by the correctly rendered entry; the `done: PR` line was an internal status-protocol line written in error and is not a team status entry
- 2026-08-29T18:15Z  derek  baseline PRD and ownership boundaries merged to main, the four workstreams now have named owners, and the stack decision is the remaining blocker before parallel work opens

- done: coordination files reconciled to four contributors
- 2026-08-29T19:38Z  andres  W2 detection design settled and recorded in DECISIONS.md: detection statistic and floors, severity as a business-impact-only function, event-time bucketing behind a watermark, canonical-schema invariants, and a minimum-operational-backend build order. Nothing here changes the published C2 or C3 contracts.
- 2026-08-29T19:38Z  andres  INTERFACES.md C2 and C3 entries refreshed: they still said "not yet specified" while docs/contracts/evidence-tools.md and docs/contracts/incident.md specify both in full. They now point at those documents, which remain the single definition.
- 2026-08-29T19:38Z  andres  GAP for derek: C2 has no time-series tool. No tool returns a metric over a series of time buckets, but C3 requires an onset, severity needs a trajectory, and the demo has to answer "since when". W2 proposes adding a metric_timeseries tool to the C2 surface; say if you would rather it be folded into cohort_metrics.
- 2026-08-29T19:38Z  andres  GAP for derek: nothing yet defines what starts an investigation. The agent runtime is decided but not its trigger. W2 proposes publishing the C3 record on lifecycle transitions only (detected, and any severity escalation), with a poll of the incident store as the always-available fallback so a transport failure cannot silently stop investigations. Needs your agreement as integration owner before either of us builds it.
- 2026-08-29T19:38Z  andres  FOR RAUL: the C1 request sent earlier assumed W1 emits a normalised event and is superseded by the 19:04Z ingestion model, since normalisation is now W2's. Three asks survive and still matter: register every native shape so W2 can write the mappers, key the raw topics so all attempts of one payment stay together and in order, and provide replayable backfill history - a contextual baseline cannot be learned from the minutes before a judge fires an incident.
