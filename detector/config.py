"""Every tunable constant in the detection plane, in one versioned place.

Thresholds are tuned against controlled scenarios; the cohort search space is
never narrowed to make a scenario pass. CONFIG_VERSION travels with every
incident record and every evidence response so any number can be explained.
"""

from __future__ import annotations

CONFIG_VERSION = "det-v1"

# --- bucketing -------------------------------------------------------------
BUCKET_SECONDS = 60
LATENESS_GRACE_SECONDS = 30

# --- detection floors ------------------------------------------------------
# All four must hold before a deviation becomes an incident. Each exists to
# suppress a specific kind of false positive.
Z_MIN = 3.0              # statistically real
ABS_DROP_MIN = 0.02      # operationally meaningful: 2 conversion points
N_PAYMENTS_MIN = 30      # enough traffic to be sure
SUSTAIN_BUCKETS = 3      # not a one-minute blip

# MEASURED, AND DELIBERATELY LEFT ALONE - the note is the finding, and the fix
# is one line in a file this change does not own.
#
# Only three of these four are floors: `SUSTAIN_BUCKETS` is never tested by
# `evaluate`. That is worth knowing, but it is not what lets healthy traffic
# raise confident incidents, and persistence is not the axis that separates
# them. Measured 2026-08-30 on a live stack with nothing injected anywhere: the
# `medium` incident raised on `{issuing_bank: Banorte, payment_method: cash,
# provider: stripe}` - a merchant-a cohort, on a run where only merchant-b was
# ever touched - carried `buckets_sustained: 4`. It was sustained. It was also
# 36 payments.
#
# What fails is `N_PAYMENTS_MIN`, and it fails because 30 is not a number of
# payments, it is a number of payments at an unstated conversion rate.
# `two_proportion_z` is a normal approximation to a binomial proportion, and
# that approximation is valid when the rarer outcome is expected at least ten
# times. On traffic converting at 95% the rare outcome is the failure, so the
# condition is n * (1 - baseline) >= 10 - about 200 payments here, not 30. Below
# it the test reports significance it has not got, and the localiser is drawn
# *toward* exactly those cells, because a deep joint cohort is both the thinnest
# slice and the one where noise looks most extreme.
#
# Measured over 160 sweeps, 45s apart, across two hours of healthy traffic with
# nothing injected, on a store whose warm-start history already matches live
# traffic:
#
#   floor as written (N_PAYMENTS_MIN = 30)          12 of 160 sweeps raised an
#                                                   incident on an uninjected
#                                                   cohort - 7.5%
#   Z_MIN 3.0 -> 3.5                                 8 of 160 - 5.0%
#   N_PAYMENTS_MIN 30 -> 150                         8 of 160 - 5.0%
#   N_PAYMENTS_MIN 30 -> 200                         0 of 160
#   n * (1 - baseline) >= 10                         0 of 160
#
# The last two are the only settings that reach zero, and only the last one is
# usable. The demo's own cohort, `{merchant_id: merchant-b, provider: adyen}`,
# runs about 180 payments in a 5-minute window: a flat floor of 200 silences the
# incident the demo exists to show, while the same cohort's expected failures are
# 180 * (1 - 0.892) = 19.4, comfortably over 10. `high_impact_small_percentage` -
# PRD section 10's small-percentage, high-money incident, the class this product
# is pitched on - measures n=2500, z=-4.43, expected failures 199.8, and clears
# either. The worst healthy false positive measured, `{card_network: visa}` at
# z=-5.00, is n=150 with 6.3 expected failures, and only the ratio floor stops it.
#
# So the floor that works is a *validity* floor, not a bigger count, and adding
# it is a change to `detect.evaluate` rather than a value here.
# Evaluated 2026-08-30 against with_provider_incident in the 5-minute detect
# window: provider-p2 has n=75, expected failures ~6.5, so the clause
# unqualifies the true cohort and build_incident reports the platform instead.
# Live merchant-b/adyen is safe (~180, ~19.4) but the guaranteed synthetic
# scenario is not. Left as the finding until the detect window or the
# synthetic volume matches the live rate.
#
#     "sample_valid": bool(observed["attempted_payments"] * (1 - expected) >= 10)
# --- baseline --------------------------------------------------------------
# v0 uses a trailing window on the same cohort. The seasonal hour-of-week
# baseline replaces this once W1 provides replayable backfill history.
BASELINE_TRAILING_BUCKETS = 60
DETECT_WINDOW_BUCKETS = 5
# Low-volume cohorts borrow their parent's rate, weighted by sample size. This
# is what stops an eight-payment cell from having a wild baseline.
SHRINKAGE_PRIOR_PAYMENTS = 50

# --- severity --------------------------------------------------------------
# Severity is a function of business impact only. Statistical strength is
# deliberately absent: a huge z-score on trivial money must stay low priority.
# Below the floor an incident is not materially expensive; above the cap extra
# money stops changing the ranking. The log runs between the two.
LOSS_RATE_FLOOR_USD_PER_HOUR = 100.0
LOSS_RATE_CAP_USD_PER_HOUR = 50_000.0

# Money is also a ceiling, not only a term. Persistence and trajectory must not
# be able to promote a trivially cheap incident on their own: an incident that
# has been losing $120/hour for an hour is still a $120/hour incident. This
# ladder is the direct encoding of PRD section 10.
SEVERITY_LOSS_RATE_CEILING = (
    (250.0, "low"),
    (2_000.0, "medium"),
    (10_000.0, "high"),
)
SEVERITY_WEIGHTS = {
    "impact": 0.55,
    "radius": 0.20,
    "persistence": 0.15,
    "trajectory": 0.10,
}
PERSISTENCE_FULL_BUCKETS = 20
SEVERITY_THRESHOLDS = (  # ordered high to low
    ("critical", 0.70),
    ("high", 0.45),
    ("medium", 0.22),
)
SEVERITY_FLOOR = "low"

# --- localisation ----------------------------------------------------------
LOCALISE_MAX_DEPTH = 3
LOCALISE_BEAM_WIDTH = 3
# A child must explain materially more than its parent to be reported instead.
# A dimension only earns a place in the reported cohort when one of its values
# is worse than the next by this margin. Contrast, not depth: if every issuer
# behind a degraded provider is equally degraded, the issuer is not part of the
# story, and this is what stops a provider-wide outage being reported as one
# arbitrary issuer inside it.
LOCALISE_MIN_SEPARATION = 0.10

# --- operational health ------------------------------------------------------
# The canonical event stream carries no health-check or CPU signal, so service
# health is *derived* from what we actually measure: the share of attempts that
# broke rather than were declined. Stating the criterion in the response is
# what keeps this a measurement rather than an opinion.
OPERATIONAL_DEGRADED_RATE = 0.05

# --- currency --------------------------------------------------------------
# A frozen table, not a live rate: a live rate would make a replay produce a
# different number than the original run.
REPORTING_CURRENCY = "USD"
FX_TO_USD = {
    "USD": 1.0,
    "COP": 0.00025,
    "BRL": 0.185,
    "MXN": 0.055,
    "EUR": 1.08,
}

# Recurrence promotes, because a fault that keeps coming back is a worse fault
# than one that happened once. The count is prior *matching* incidents on the
# same cohort inside the lookback, counted as episodes rather than rows (see
# RECURRENCE_EPISODE_GAP_SECONDS). It is deliberately NOT the same figure as
# `incident_history`'s `recurrence.prior_matching_incidents`, which stays a
# plain count of the rows it lists over an operator-chosen window. The ladder mirrors
# SEVERITY_LOSS_RATE_CEILING in shape and points the other way: that one caps a
# band, this one lifts it, and both are read after the weighted sum.
RECURRENCE_LOOKBACK_SECONDS = 6 * 3600
# Rows are not episodes. Onset is measured from the rolling detect window, so a
# single continuous fault drifts its onset - and the incident id derived from it
# - and lands in the table as two or more rows. Counting rows would let ONE
# rehearsal of ONE injection satisfy the two-prior threshold below and promote
# the next run a band. So a prior row only counts as a prior *episode* when the
# cohort was quiet between them: its `last_seen_epoch` must be at least this far
# before this incident's `onset_epoch`. The bound to clear is the drift of one
# continuous episode, which cannot exceed one sweep plus the detect window
# (DETECT_WINDOW_BUCKETS + SUSTAIN_BUCKETS = 8 buckets); 15 minutes leaves
# headroom and stays far below the 6-hour lookback, so two genuinely separate
# faults an hour apart still count as two.
RECURRENCE_EPISODE_GAP_SECONDS = 15 * 60
# A `watching` row is a near-miss we deliberately chose not to page on, so it
# must never lift a later band. Every ordinary downstream state - claimed,
# investigating, diagnosed, mitigated, resolved - is a genuine prior recurrence
# and keeps counting.
RECURRENCE_EXCLUDED_LIFECYCLE_STATES = ("watching",)
SEVERITY_RECURRENCE_PROMOTION = (  # (prior matching incidents, bands promoted)
    (2, 1),
    (4, 2),
    (8, 3),
)

# --- merchant-relative severity ---------------------------------------------
# The dollar ladder above asks one question of every merchant on the platform:
# how many dollars an hour. An airline and a fast-food chain do not answer it
# on the same scale, so a chain losing sixty percent of its own traffic can sit
# under $2,000/hour and be capped at `medium` - it never rings a phone.
#
# So the loss is also expressed as a share of that merchant's own normal hour,
# and read on its own ladder. The effective ceiling is whichever of the two
# bands is HIGHER, which is what lets a genuinely enormous absolute loss rank
# on a large merchant and a proportionally catastrophic one rank on a small.
#
# The thresholds: below 2% of a normal hour the loss is inside the ordinary
# hour-to-hour variance of any merchant's traffic and is not an outage; 10% is
# a material dent somebody should see on a board but not be woken for; above
# 35% more than a third of that merchant's revenue for the hour has stopped
# arriving, which is a page for a merchant of any size.
SEVERITY_LOSS_SHARE_CEILING = (
    (0.02, "low"),
    (0.10, "medium"),
    (0.35, "high"),
)
# A normal hour cannot be learned from a handful of minutes: traffic is
# diurnal, and a short store would hand back the incident's own hour as the
# merchant's normal. Below either floor the merchant's normal is *unknown* and
# severity falls back to the dollars-only ladder, which is today's behaviour
# exactly.
MERCHANT_NORMAL_MIN_HOURS = 6.0
MERCHANT_NORMAL_MIN_PAYMENTS = 200

# --- leading indicators -----------------------------------------------------
# A provider does not fail instantly. Latency rises, timeouts appear in the
# decline mix, retries amplify, queues build, and conversion falls last. These
# are the floors for calling the earlier signals materially degraded against
# the same trailing baseline the conversion test already uses. Nothing here is
# trained, fitted or forecast: it reports that a cohort is degrading now, and
# never a future number.
FORMING_TIMEOUT_SHARE_DELTA = 0.05    # +5 points of timeout share over baseline
FORMING_LATENCY_P95_RATIO = 2.0       # latency twice its baseline
# 1.5 fires on healthy traffic, and it still does once the warm-start history
# matches live latency - the seam was never the whole story. The reading compares
# *means* over a distribution with a 2s-8s error tail (the constant is named for
# a p95 the code does not compute), so one slow attempt moves a small cohort.
# Measured 2026-08-30 over 1,080 healthy cohort readings, 40 sweeps x 30 cohorts,
# on a store whose history already matches live traffic: median 1.052, p90 1.246,
# p99 1.543, max 1.672, with 18 readings crossing 1.5 and none reaching 1.75.
# 2.0 sits above every healthy reading with margin. It costs no sensitivity that
# matters: `effect=latency` publishes 6000ms against a ~350ms baseline, a ratio
# near 17, so the floor could be four times higher and still catch it.
FORMING_LATENCY_MIN_BASELINE_MS = 50.0  # below this a ratio is noise, not a signal
# A cohort routed around entirely shows no declines at all - its volume simply
# goes to zero, and a cohort with no traffic can never clear N_PAYMENTS_MIN. So
# volume is compared to its own trailing baseline too, at the same bucket rate.
FORMING_VOLUME_COLLAPSE_RATIO = 0.25  # under a quarter of its own normal rate
FORMING_VOLUME_BASELINE_MIN = 60      # payments of trailing history before we judge it
# Latency and timeout are only judged when the recent rate is comparable to
# the trailing rate. A detector that just replayed Kafka from offset zero
# sees a 30x volume spike against a thin trailing hour; that is warmup, not
# a forming outage. Volume collapse is the opposite signal and does not use
# this band - zero traffic is the finding.
FORMING_VOLUME_COMPARABLE_MIN = 0.5
FORMING_VOLUME_COMPARABLE_MAX = 2.0

# --- the watch -------------------------------------------------------------
# The near-miss predicate, beside the four detection floors rather than inside
# them. All of it must hold, and none of it lowers the bar for an incident: a
# watch is a separate, quieter state that never pages.
#
# Tuned against the measured near-miss the pivot is built on: z -2.3 is watched
# and z -1.0 is not, so a real developing deviation appears inside the four
# minutes before the cliff while ordinary minute-to-minute noise does not. A
# looser bar floods the dashboard; a tighter one never fires in time.
WATCH_Z_MAX = -1.5
WATCH_ABS_DROP_MIN = 0.03
WATCH_TRAJECTORY = 1  # worsening; a recovering dip is not worth a warning
# 0.01 was inside ordinary variation and 0.05 was above the thing this clause has
# to report, so both ends were measured before settling here.
#
# The clause is a *floor on the same 5-bucket window the z-score reads*, so what
# matters is not where the deviation ends up but where it has got to by the time
# the warning is due. Measured 2026-08-30 on the live stack, injecting the judge's
# `developing` stage on merchant-b/adyen and sampling the cohort every 30s: the
# window fills linearly, reaching 0.0204 at 1 minute, 0.0242 at 2, 0.0309 at 3 and
# about 0.051 once the whole window is inside the deviation at 5. `make verify-demo`
# gives that stage 240 seconds. At 0.05 the drop clause is not met until roughly
# 4.9 minutes - the warning is late, not absent, which is exactly what the 07:59Z
# run recorded as silence. At 0.03 the drop clause is met by 2.9 minutes and the
# z-score becomes the binding clause again, which is what this predicate says it
# wants: "the z-score is what separates a real developing deviation from noise,
# and the drop is what keeps a statistically clean but operationally meaningless
# wobble out". A 3-point drop is still operationally meaningful and still above
# ordinary variation - and a watch row that fires on a quiet cohort costs the
# board a card that says NOT AN INCIDENT YET, where the same mistake one ladder
# up costs it a confident incident on an innocent merchant.
#
# The number is not a preference between the two ends, because at 0.05 there is
# no magnitude left to choose. Sweeping every injected magnitude from 0.05 to
# 0.40 against this floor and the detection floors together, and asking for one
# that warns inside 240 seconds and never crosses Z_MIN:
#
#   0.05   no magnitude qualifies - the band is EMPTY
#   0.04   0.13 warns at 185s, 0.14 at 170s, 0.15 at 160s
#   0.03   0.12 warns at 190s, 0.13 at 175s, 0.14 at 160s, 0.15 at 150s
#   0.02   identical to 0.03 - the drop clause has stopped binding
#
# 0.05 is not a strict floor, it is an empty intersection: it puts the drop a
# deviation must reach above the drop any deviation *can* reach while still
# being a near-miss, so the warning beat of the pitch cannot exist at any
# magnitude. 0.03 is the largest value at which the z-score is the clause that
# decides, which is what this predicate is written to want, and where
# `surfaces.inject.STAGE_DEVELOPING` keeps the most room under Z_MIN.
