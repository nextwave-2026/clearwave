# Clearwave

A monitoring and diagnosis system for payment conversion drops: detect the ones that matter,
isolate the root cause, explain it with evidence, and recommend an action without executing it.

**Simulated demo data.** All merchants, banks, payments, incidents and outages shown in this
repository and in the live demo are simulated data produced by this project's simulator for
demonstration. Nothing shown represents or implies a real incident, outage, or service problem at
any named company. Real company names are used only to make the demonstration recognisable and
realistic.

## Problem

Payment orchestration platforms see every transaction between merchants and providers. Conversion
- the share of attempted payments that get approved - is the metric that moves the most money. It
drops silently for a thousand reasons: a degraded provider, an issuing bank over-declining, a
method down in one country, a change nobody announced. Every lost point is money lost by the
minute.

Detection today is artisanal: a human watching dashboards. Classic alerts fail at both ends - they
fire on everything and get ignored, or fire on nothing. By the time someone notices, hours have
passed. Detection is the easy part. Diagnosis is the hard part, because the answer is scattered
across thousands of transactions and a tired human assembles it by crossing filters at 3 a.m.

This is Challenge 02, Control Tower, at NextWave Hackathon 2026 (Bogota site, Yuno x Nauta,
supported by OpenAI). The pick is final. The full brief is in [`docs/challenge.md`](docs/challenge.md).

## Solution

What is decided: a system that watches a live payment stream, detects conversion drops that
matter, diagnoses the root cause across the transaction dimensions with visible evidence, explains
it in operations language with an estimated money cost, prioritises concurrent incidents, states
honestly when evidence is insufficient, and recommends an action without executing it. It has two
cooperating planes: deterministic detection and agentic investigation. It diagnoses, not remediates;
severity is independent of diagnostic confidence; and honest uncertainty is required behaviour.

What is still open: the stack, transport, persistence, and the items listed in
[`docs/ownership.md`](docs/ownership.md). The detection and diagnosis approach is settled by the
PRD. Do not add a package manifest, lockfile, Dockerfile, or application scaffold until a human
decides the stack and records it in `DECISIONS.md`.

### Product baseline

[`docs/prd.md`](docs/prd.md) is the authoritative product baseline. [`docs/ownership.md`](docs/ownership.md)
defines the four-way work division.

## Demonstration

The product is runnable. The copy-pasteable operator sequence - pre-warmed live stack, offline stage path, and live Kafka caveats, including commands that fail and must not be used - is in [`docs/demo-sequence.md`](docs/demo-sequence.md).

Live product path, from the repository root. `make stack-up` writes eight hours of healthy merchant-b/adyen history into the store (seconds, not hours of waiting) and then starts the compose loop. Detection baseline and merchant-relative severity are warm when it returns. The anomaly still happens live after the judge presses the button.

```sh
make stack-up
```

Open the URL it prints (`http://127.0.0.1:8082/`). `make stack-status` reports each piece of the loop, including whether the store is warm enough for the trailing-hour baseline and for the six-hour merchant-relative floor, separately. `make stack-down` stops it. Do not run `make e2e` then `make surfaces-serve` as the pitch; that is a scripted CLI demonstration.

Safe stage path if Docker is not up, from the repository root after `make install`. Two terminals, same `$DB`. Port `18080` avoids a busy `8080`; the product default is `8080`.

Terminal A:

```sh
DB=/tmp/clearwave-demo.db
PORT=18080
rm -f "$DB" "$DB-wal" "$DB-shm"
CLEARWAVE_SURFACES_QUIET=1 .venv/bin/python -m surfaces.server \
  --host 127.0.0.1 --port "$PORT" --db "$DB"
```

Terminal B (requires `OPENAI_API_KEY` in the environment; do not print it):

```sh
DB=/tmp/clearwave-demo.db
.venv/bin/python -m investigation.vertical --db "$DB"
```

Open `http://127.0.0.1:18080/`. Wait for `Lifecycle after investigate: diagnosed`.

Use `.venv/bin/python`, not system `python3`. Do not run `--mode anomaly`, system `python3 -m pip install -r detector/requirements.txt`, or `make live` as a one-step demo. They fail. The judge button is a working toggle, but it needs the broker and the compose workers up; with no broker it says so rather than claiming an incident fired.

## Architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the settled product architecture; technology choices remain open.

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

No third-party dependencies have been added yet. The generated record lives in
[`LICENCES.md`](LICENCES.md). Run `make licences` before submission; it works offline.

## Team

- Derek Sarmiento Loeber - GitHub [`DereKk8`](https://github.com/DereKk8), coordination handle
  `derek`. Systems engineering student at Pontificia Universidad Javeriana, Bogota; backend and
  MLOps, ML infrastructure, web development. Repository admin.
- Andres Felipe Cruz Torres - GitHub [`andresfelipe0711`](https://github.com/andresfelipe0711),
  coordination handle `andres`. Junior data analyst working toward data science; data science,
  machine learning and AI. Repository admin.
- Juan Camilo - GitHub [`juank115`](https://github.com/juank115), coordination handle `juank`.
  Software engineer from Colombia, currently doing a master's degree in artificial intelligence
  and computer science. Repository write access.
- Raul Higuera - GitHub [`raulhiguerac`](https://github.com/raulhiguerac), coordination handle
  `raul`. Based in Bogota. Repository write access; organisation and repository invitations were
  pending acceptance when this was written.
