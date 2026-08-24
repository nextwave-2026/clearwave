# Clearwave

Prompt: Give a one-sentence description of the problem this addresses. Replace this line once the
problem is known.

## Problem

Prompt: Describe who has the problem, why it matters, and the evidence or event context behind it.

## Solution

Prompt: Explain what was built, how it works at a useful level, and what outcome it provides.

## Demonstration

Prompt: Give the commands or steps a reviewer can follow to see the essential behaviour.

## Architecture

Prompt: Link to or embed the current Mermaid architecture diagram and call out the important boundaries.

## Pre-existing components

This repository incorporates a preparation kit that was **authored before the event**, in its own
separate repository at https://github.com/nextwave-2026/nextwave-kit, and is declared as pre-existing
intellectual property.

The incorporated portion is infrastructure only, and contains no domain logic:

- `Makefile` - fixed build, test and inventory target names.
- `.github/workflows/ci.yml` - continuous integration workflow.
- `scripts/licences.py` - offline dependency and licence inventory generator.
- `.gitattributes`, `DECISIONS.md`, `INTERFACES.md` - coordination files and their merge configuration.
- `LICENCES.md` - licence policy and generated inventory.
- `README.md`, `ARCHITECTURE.md`, `docs/pitch.md` - document templates.

Prompt: add every third-party component adopted during the event below, with its licence. The complete
generated record lives in `LICENCES.md`; run `make licences` before submission.

## Licence inventory

Prompt: Summarise the generated licence inventory and link to the complete record in `LICENCES.md`.

## Team

Prompt: List the contributors and their relevant responsibilities for the entry.
