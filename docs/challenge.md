# Challenge 02 - Control Tower

Restatement of the organisers' published brief for NextWave Hackathon 2026, Challenge 02. It adds
nothing they did not state and does not soften anything they did.

- Challenge: https://nextwave-hackathon-2026.vercel.app/challenges/file-02
- Protocol that applies to every challenge: https://nextwave-hackathon-2026.vercel.app/challenges
- Judging: https://nextwave-hackathon-2026.vercel.app/judging
- Timeline: https://nextwave-hackathon-2026.vercel.app/schedule

This crew is committed to Challenge 02. Under protocol SYS.A (pick one) the pick is final.

## Event and timeline

NextWave Hackathon 2026, Bogota site (Yuno x Nauta, supported by OpenAI). 24-hour build window.
Times below are Bogota local, from https://nextwave-hackathon-2026.vercel.app/schedule.

Saturday 29 August 2026:

- 09:00 - check-in, doors open (T-01:30)
- 09:30 - OpenAI opening talk (T-01:00)
- 10:00 - challenges announced (T-00:30)
- 10:30 - T-ZERO, coding starts

Sunday 30 August 2026:

- 10:30 - code freeze, submissions locked (T+24:00)
- 11:00-13:00 - pitches, 7 minutes per team (T+24:30)
- 13:30 - city champions announced (T+27:00)
- 15:30 - winners announced (T+29:00)

## Scenario

A payment orchestration platform. Merchants process their payments through several providers, and
the platform sees every transaction from all of them.

## Key definitions

- **Merchant:** a company that collects payments through the platform.
- **Provider:** external processor that handles the payment (Stripe, Adyen, dLocal, MercadoPago).
- **Payment method:** card, PSE, wallet, PIX, cash-in-store.
- **Conversion (approval rate):** percentage of approved payments over attempted payments - the
  metric that moves the most money.
- **Issuing bank:** the bank that issued the buyer's card; it can decline on its own.
- **Decline code:** the reason the provider returns when a payment is not approved.
- **Dimensions of a transaction:** merchant x provider x method x country x issuing bank x decline
  code - the diagnosis lives in those intersections.
- **Root cause:** the real origin of the problem, not the symptom ("provider X declines bank Y's
  cards in Brazil since 14:03", not "conversion dropped").

## The problem

Conversion drops silently and for a thousand different reasons: a degraded provider, an issuing
bank over-declining, a method down in one country, a change nobody announced. Every lost point of
conversion is money lost by the minute. Detection today is artisanal:

- A human looks at dashboards when they can.
- Classic alerts fail at both ends: they either fire on everything (and get ignored) or on nothing.
- By the time someone notices the drop, hours have passed.

Detecting is the easy part. The hard part is the diagnosis: is the drop a provider's, a method's, a
country's, an issuing bank's, a merchant's? The answer is scattered across thousands of
transactions, and today a tired human assembles it by crossing filters at 3 a.m.

## Objective

Build a monitoring and diagnosis system that:

- **Watches** a live transaction stream and detects conversion drops that matter, distinguishing
  them from normal noise (time of day, weekends, statistical variance).
- **Diagnoses the root cause** by navigating the dimensions (merchant x provider x method x country
  x issuing bank x decline code) until it isolates where the problem is.
- **Explains with evidence:** what dropped, since when, who it affects, how much money it is
  costing, and why the system believes that - in language an operations person understands.
- **Prioritises** when several things happen at once, and honestly says when the evidence is not
  enough.
- **Recommends** an action for a human - without executing it. This challenge diagnoses, it does
  not remediate.

May include (not limited to): estimating the money cost of each incident; comparison against
expected historical behaviour; memory of past incidents to recognise repeats.

## Trial by fire

The judges will inject a live incident the team never rehearsed - a new combination of dimensions.
The system must detect and diagnose it correctly in front of everyone, without the team touching
anything.

## Expected results

A demo showing:

- A mocked payment stream running normally, and the system not firing on noise.
- A real drop injected live, detected in reasonable time.
- The correct root-cause diagnosis, with the evidence visible: what, where, since when, who is
  affected.
- A readable explanation, plus the estimated cost, plus the recommended action.
- A case with two simultaneous incidents, correctly separated and prioritised.
- The trial by fire passed.

## Bonus points

- A case where the system admits the evidence is not enough, instead of inventing a diagnosis.
- Recognising a repeated incident ("this already happened on Tuesday") using memory.
- An explanation consumable by two audiences: operations (detail) and an executive (one line with
  the money).

## Minimal fictional case

PagoTotal, an orchestrator processing payments for 3 merchants with 3 providers in Mexico,
Colombia and Brazil. Data volumes are invented and extensible.

Key moments:

1. Normal operation - the system watches and does not bother anyone.
2. A provider starts over-declining only in Brazil - detection plus diagnosis.
3. At the same time, a Mexican issuing bank goes down for a single merchant - the system separates
   the two stories and prioritises them.
4. The judges inject their own incident (trial by fire).

Transactions, decline codes, dashboards and history may all be invented.

## Rules that apply to every challenge

From the challenges protocol at https://nextwave-hackathon-2026.vercel.app/challenges. The SYS.A /
SYS.B / SYS.C labels here are the event-wide protocol. The judging page reuses the same labels for
different statements; those are in the next section.

**SYS.A - Pick one.** Each crew tackles exactly one of the four challenges. The pick is final.

**SYS.B - Invent freely.** Data, flows, APIs and databases may be invented. Frameworks and
protocols are free - draw inspiration from existing ones or design your own - but you must be able
to defend every choice.

**SYS.C - Trial by fire.** Judges will operate the system live, with unrehearsed input, in front of
everyone. It must react correctly without the team touching anything.

## How the jury evaluates

From https://nextwave-hackathon-2026.vercel.app/judging. The three principles below are also
labelled SYS.A / SYS.B / SYS.C on that page. They are not the protocol rules above.

### Three principles

**SYS.A - Depth over difficulty.** Picking the hardest challenge earns nothing by itself. A modest
scope solved deeply beats an ambitious scope solved superficially.

**SYS.B - Working beats promised.** They evaluate what runs in front of them, live, not what the
slides say it will do.

**SYS.C - Judgment beats spectacle.** The technical defense weighs as much as the demo. A
spectacular demo the team cannot explain loses to a simpler demo defended with clear reasoning.

### Five lenses

Roughly in order of weight. None of them alone decides a winner.

1. **Does it work?** Does the system run end to end and pass the trial by fire - reacting correctly
   to what the judges change live, without the team touching anything?
2. **Depth and judgment.** Is the architecture sound? Can the team explain every major decision,
   the alternatives they rejected, and why? Does the decision log show real trade-offs?
3. **Solves the real problem.** Does it hit the challenge's objective as written - including the
   ugly cases - rather than a generic product that happens to sit nearby?
4. **Originality.** Is there an idea not seen before - an approach, an insight, a mechanism - or is
   it the obvious solution executed adequately?
5. **Experience and clarity.** Would a human on the other side actually use it? Is the pitch clear,
   the demo legible, the repo readable by someone who was not there?

### What does not score

- Number of features, slides, integrations or lines of code.
- Buzzwords. Naming a framework is not a design decision - knowing why you chose it is.
- A polished video of something that does not run live.
- Building for the rubric. Teams that chase these five lenses one by one usually end up with a
  shallow project on all five.

### Organisers' advice

Get the thinnest possible version working end to end in the first hours. Then spend the rest of
the 24 hours making it deep - handling the ugly cases, understanding your own trade-offs, and
rehearsing the trial by fire. Teams that do this in the other order run out of time with a
beautiful front and nothing behind it.

### Judging format

Sunday 30 August, pitches right after code freeze. Each team: short pitch, live demo, trial by
fire, technical Q&A - 10 minutes per team. City champions give a 15-minute final pitch (10
presentation + 5 questions). Judges are from Yuno and Nauta. Every project is seen by the full
panel; judges rank independently, then deliberate together. Missing deliverables are noticed.

The schedule page lists pitches as 7 minutes per team; the judging page lists 10 minutes per team
covering pitch, demo, trial by fire and Q&A. Both figures are the organisers'.

## Deliverables

1. Presentation (slides) - in this repository, `docs/pitch.md`.
2. Demo (live or video).
3. Public GitHub repository with README.
4. Architecture diagram - in this repository, `ARCHITECTURE.md`, kept as Mermaid text, not an
   image.
5. Decision log - alternatives considered and why we chose what we chose.

The technical defense weighs as much as the demo. A spectacular demo the team cannot explain loses
to a modest demo defended with judgment.
