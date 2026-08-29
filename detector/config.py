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
