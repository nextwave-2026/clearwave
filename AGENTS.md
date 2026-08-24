# Clearwave - agent orientation

Read this before touching anything. It is the orientation file for every agent working in this
repository, on either side of the team.

## What this is

Clearwave is a competition entry repository. Two developers work on it from two independent machines,
each running their own supervisor and their own workers. There is no shared supervisor between the two
sides, so **this repository is the only coordination channel that exists.**

The name is deliberately product-like and domain-agnostic. Do not rename it to match the problem.

## The challenge is not known yet

Nothing in this repository commits to a language, framework, or architecture, and that is intentional.
The problem statement is revealed at the start of the build window. Until then:

- Do not add a package manifest, lockfile, deploy configuration, or Dockerfile.
- Do not add domain logic.
- Do not "helpfully" scaffold an application.

If you believe something in that list is now needed, it is because the challenge has been revealed and
a human decided the stack. Confirm that before adding it.

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
signal that both sides are editing the same boundary and must reconcile.

**Every entry names its host.** With two independent sides and no shared supervisor, an unattributed
line is close to useless.

### When to write to which

- Finished something the other side should know about → `STATUS.md`.
- Made a call the other side must now build around → `DECISIONS.md`, including what they must do
  differently.
- Changed the shape of a boundary → update `INTERFACES.md` in place.

Writing to `STATUS.md` at every milestone is the single habit this whole arrangement depends on. A file
nobody remembers to write to is worse than no file, because both sides trust it.

## The Makefile contract

Targets are `install`, `lint`, `test`, `build`, `licences`, and `ci`. The continuous integration
workflow calls them **by name**.

- Fill in target bodies as the stack is chosen.
- **Never rename a target**, or CI breaks.
- Never edit the workflow to work around a target. Fix the target.

`install`, `lint`, `test` and `build` are currently placeholders that exit 0, so CI is green from the
start. `licences` already works.

## Licences - a hard rule, not hygiene

Strongly copyleft licences are prohibited: **no GPL, no AGPL**, no licence that would force the
organisers to release their own code. Permissive licences are fine (MIT, Apache-2.0, BSD, ISC).

Every third-party component must be identified in the documentation - that is a graded deliverable, not
a nicety. Run `make licences` to regenerate the inventory in `LICENCES.md`; it works offline and never
needs network access.

**If you are about to add a dependency, check its licence first.** Reaching for a convenient AGPL
library late at night is the realistic way this goes wrong.

## Pre-existing intellectual property - do not break this

Part of this repository was authored **before the event** in a separate repository
(https://github.com/nextwave-2026/nextwave-kit) and is declared pre-existing IP. Ownership of work done
*during* the event transfers to the organisers; pre-existing work does not.

That protection depends on two things staying true:

1. **The histories must never merge.** Content was copied, not forked. Never add the kit as a remote,
   never merge or rebase from it, never cherry-pick from it.
2. **The declarations must survive.** The first commit message and the README "Pre-existing components"
   section both declare the provenance. Never remove or weaken either.

## Working conventions

- Never commit directly to `main`. Branch, then open a pull request.
- Never merge a red pull request. Green checks only, either side.
- Do not discard work that has not landed. If something looks stale, ask rather than delete.
- Both sides may merge their own green work without waiting for the other.

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
