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
# same cohort inside the lookback - the number `incident_history` already
# publishes as `recurrence.prior_matching_incidents`. The ladder mirrors
# SEVERITY_LOSS_RATE_CEILING in shape and points the other way: that one caps a
# band, this one lifts it, and both are read after the weighted sum.
RECURRENCE_LOOKBACK_SECONDS = 6 * 3600
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
FORMING_LATENCY_P95_RATIO = 1.5       # p95 latency half again its baseline
FORMING_LATENCY_MIN_BASELINE_MS = 50.0  # below this a ratio is noise, not a signal
# A cohort routed around entirely shows no declines at all - its volume simply
# goes to zero, and a cohort with no traffic can never clear N_PAYMENTS_MIN. So
# volume is compared to its own trailing baseline too, at the same bucket rate.
FORMING_VOLUME_COLLAPSE_RATIO = 0.25  # under a quarter of its own normal rate
FORMING_VOLUME_BASELINE_MIN = 60      # payments of trailing history before we judge it

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
WATCH_ABS_DROP_MIN = 0.01
WATCH_TRAJECTORY = 1  # worsening; a recovering dip is not worth a warning
