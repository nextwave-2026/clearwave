# Clearwave - agent orientation

Read this before touching anything. It is the orientation file for every agent working in this
repository.

## What this is

Clearwave is a competition entry repository. Four people work on it from independent machines,
each running their own supervisor and their own workers. There is no shared supervisor across
machines, so **this repository is the only coordination channel that exists.**

The name is deliberately product-like and domain-agnostic. Do not rename it to match the problem.

## Challenge 02 is the committed pick

The team is committed to Challenge 02, Control Tower. The pick is final under the event protocol
SYS.A (pick one). The full brief is in `docs/challenge.md`.

Nothing in this repository commits to a language, framework, or architecture yet. The prohibition
on package manifests, lockfiles, deploy configuration, Dockerfiles, and application scaffolding
lifts only once a human has decided the stack. The stack is not decided. Confirm that decision
before adding anything that commits us to a language or framework.

## The four coordination files, and why they differ

| File | Shape | Merge behaviour |
| --- | --- | --- |
| `AGENTS.md` | Durable orientation | Edited in place |
| `STATUS.md` | Progress log, newest at bottom | **Union** - never conflicts |
| `DECISIONS.md` | Decision log, newest at bottom | **Union** - never conflicts |
| `INTERFACES.md` | Current shape of each boundary | In place - conflicts are a signal |

`STATUS.md` and `DECISIONS.md` are declared `merge=union` in `.gitattributes`. Union merge keeps both
sides' added lines instead of raising a conflict, which is what lets two people append freely while
working in parallel. Both are **append-only**: never edit or delete an existing entry, correct a past
one by appending a new one.

`INTERFACES.md` is deliberately NOT union-merged. Union would keep both the old and the new value of an
interface, leaving the file asserting two contradictory things at once. A conflict there is a genuine
signal that two contributors are editing the same boundary and must reconcile.

**Every entry names who wrote it**, using that person's handle - `derek`, `andres`, `juank`, or
`raul`. With independent machines and no shared supervisor, an unattributed line is close to
useless, and a real name tells you who to go and talk to.

### When to write to which

- Finished something everyone else should know about → `STATUS.md`.
- Made a call everyone else must now build around → `DECISIONS.md`, including what they must do
  differently.
- Changed the shape of a boundary → update `INTERFACES.md` in place.

Writing to `STATUS.md` at every milestone is the single habit this whole arrangement depends on. A file
nobody remembers to write to is worse than no file, because everyone trusts it.

## The Makefile contract

Targets are `install`, `lint`, `test`, `build`, `licences`, and `ci`. The continuous integration
workflow calls them **by name**.

- Fill in target bodies as the stack is chosen.
- **Never rename a target**, or CI breaks.
- Never edit the workflow to work around a target. Fix the target.

`install`, `lint`, `test` and `build` are currently placeholders that exit 0, so CI is green from the
start. `licences` already works.

## Licences - a hard rule, not hygiene

Permissive licences are fine (MIT, Apache-2.0, BSD, ISC, and similar licences). Weak, file-level copyleft - specifically MPL-2.0 - is accepted. The obligation attaches to modifications of the MPL-licensed files themselves; it does not reach our own code and does not oblige the organisers to release theirs. Strong copyleft remains prohibited: **no GPL, no AGPL**, and no licence that would force the organisers to release their own code. Any licence outside these named categories must be raised rather than assumed acceptable.

Every third-party component must be identified in the documentation - that is a graded deliverable, not
a nicety. Run `make licences` to regenerate the inventory in `LICENCES.md`; it works offline and never
needs network access.

**If you are about to add a dependency, check its licence first.** Reaching for a convenient AGPL
library late at night is the realistic way this goes wrong.

Regenerate with `python3 -S scripts/licences.py`, not a bare `make licences`. The generator reads licence
metadata from whatever interpreter runs it, so a machine with packages installed in the system Python emits
transitive rows CI's clean interpreter will not, and `scripts/ci/licences_current.sh` then fails on an
inventory that looked right locally. The guard itself runs `make licences`, so it **rewrites `LICENCES.md`
as a side effect** - run it and then `git add -A` and you commit your machine's inventory without noticing.
If you changed no dependency, `git checkout origin/main -- LICENCES.md` before committing.

## Pre-existing intellectual property - do not break this

Part of this repository was authored **before the event** in a separate repository
(https://github.com/nextwave-2026/nextwave-kit) and is declared pre-existing IP. Ownership of work done
*during* the event transfers to the organisers; pre-existing work does not.

That protection depends on two things staying true:

1. **The histories must never merge.** Content was copied, not forked. Never add the kit as a remote,
   never merge or rebase from it, never cherry-pick from it.
2. **The declarations must survive.** The first commit message and the README "Pre-existing components"
   section both declare the provenance. Never remove or weaken either.

## Integration contract - read before building

Before changing anything under `stubs/` or implementing any layer, read `docs/integration-guide.md`.
The stubs are binding seams, not disposable examples: replace only your own layer in place.
`python3 stubs/slice.py` must keep running and printing all five stages at every commit.
Contracts change only through `INTERFACES.md` and `DECISIONS.md`, with the change announced in `STATUS.md`.
Do not delete stubs, weaken guards, or silently change a shape.

The C2 evidence tools under `stubs/evidence/` are no longer fixtures: ten of the eleven measure one
SQLite store, located by `CLEARWAVE_DB` and defaulting to `state/clearwave.db`. Every consumer must
read that same file or the system gives two answers to one question. `python3 -m detector seed &&
python3 -m detector detect` fills a store; an empty one answers with zeros rather than failing. The
behaviour is specified in `docs/contracts/evidence-tools.md`.

`python3 -m investigation.vertical --investigate-only` (or `make investigate DB=...`) is the command that
investigates an incident a store already holds - a live consume included - without reseeding it. The bare
entry point still runs seed then detect then investigate.

W2 also consumes W1's three live Kafka topics into that same store (`python3 -m detector consume --detect`).
The live and file-based paths share one normalisation and one store; the file-based path imports no Kafka
client and is the broker-free demo fallback. Consumer operator detail: `docs/live-ingestion.md`, and
`make e2e` runs the whole live chain. The copy-pasteable demo runbook - offline stage path, live Kafka
caveats, and commands that fail - is `docs/demo-sequence.md`. Use `.venv/bin/python`. Do not use
`--mode anomaly` or system pip, and do not treat `make live` as a one-step demo: it is the consume
step alone and starts neither Kafka nor a worker.

The dashboard's judge toggle is the one thing in `surfaces/` that writes: it calls `worker.inject` to
publish a start or stop command to W1's control topic, changing a *running* worker with no restart. Never
reimplement that command shape and never let a scenario identifier cross it. What it fires is one named
constant, `surfaces.inject.INJECTED_INCIDENT`. When the broker is unreachable it must say so rather than
report a scenario that did not fire. Inject only after the target worker is publishing - `incidents.control`
starts from latest, so an earlier command is silently lost.

## Working conventions

**Code** goes through a branch and a pull request. Never commit code directly to `main`.

**The coordination files are the deliberate exception. Commit them straight to `main`.**
`STATUS.md`, `DECISIONS.md`, `INTERFACES.md` and this file exist so the four contributors stay in step, and a
pull request round trip defeats that: a status line everyone else needs now is useless sitting in
review. Append, commit, push to `main`, done - no branch, no pull request, no waiting.

That is safe here precisely because of the merge configuration. `STATUS.md` and `DECISIONS.md` are
union-merged, so contributors appending at the same moment cannot conflict. Verified: with contributors
committing to `main` from a stale base, the second push is rejected, a plain `git pull` merges cleanly,
and both entries survive. So the recovery when your push is rejected is simply:

```sh
git pull --no-rebase && git push
```

Two caveats worth knowing rather than discovering:

- **Union merge does not preserve order.** Entries interleave by merge order, not by timestamp. That is
  why every entry carries an ISO 8601 timestamp - the timestamp is the sort key, not the position.
- **`INTERFACES.md` is edited in place, so it CAN conflict** even committed directly. That conflict is
  the signal, not a nuisance: two people are changing the same boundary and need to talk. Resolve it by
  agreeing, not by picking a side blind.

### CI coordination guards

CI now enforces that all six contracts stay present and owned, coordination logs remain append-only and attributed, the pre-existing IP declaration survives, required Makefile target names survive, and the licence inventory stays current. It also surfaces overlapping open pull requests as an advisory warning for contributors to coordinate.

Other conventions:

- Never merge a red pull request. Green checks only, either side.
- Do not discard work that has not landed. If something looks stale, ask rather than delete.
- Any contributor may merge their own green code work without waiting for the others.

## Required deliverables

Four things are graded: a presentation (`docs/pitch.md`), a working demonstration, this public
repository with its `README.md`, and an architecture diagram (`ARCHITECTURE.md`, a Mermaid block - keep
it text, not an image).

Three of the four are templated here already. **The presentation must exist before the code freeze**,
because pitching begins shortly after it - somebody stops writing code well before the deadline.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## Competition coordination skills

See `.agents/skills/`: `claim-before-build` checks claims before coding, `boundary-conflict-stop` halts interface contradictions, `decision-capture` records consequential choices, and `hold-under-fire` verifies working claims under unrehearsed use. `.claude/skills` points to the same directory for harness compatibility.
