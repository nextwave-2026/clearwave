"""The demo's opening, as a relationship between two files rather than two numbers.

`surfaces/inject.py` chooses how large a deviation the judge's controls publish.
`detector/config.py` chooses how large a deviation has to be before the board
says anything. Neither file can be read on its own to know whether the judge
presses a button and sees a warning, an incident, or nothing at all - and the
demo has now failed both ways on numbers that each looked defensible alone:
`STAGE_DEVELOPING` at 0.35 produced five HIGH incidents where a warning belonged
(#77), and at 0.12 against a 0.05 watch floor it produced four minutes of
silence (verification run 2026-08-30T07:59Z).

So the relationship is pinned here, in the only terms that decide it.

THE MEASUREMENT
---------------
Taken 2026-08-30 against a live isolated stack (compose project
`clearwave-verify-demo`), on the cohort the judge's controls target:

  cohort                {merchant_id: merchant-b, provider: adyen}
  payments per window   157-186 over the 5 buckets of DETECT_WINDOW_BUCKETS
  baseline conversion   0.890-0.892
  trailing baseline     ~1960 payments over BASELINE_TRAILING_BUCKETS

and, injecting a `decline` effect at probability p and sampling the cohort
every 30 seconds, the conversion drop grows linearly as the deviation fills the
window, reaching `RESPONSE * p` when the whole window is inside it:

  p=0.10   0.0204 at 1 min   0.0242 at 2 min   0.0309 at 3 min   ~0.051 at 5 min

RESPONSE is well under 1 because a payment is an attempt chain: W1 retries an
affected decline away from the provider that declined it, so some of what the
injection declines still converts, and the cohort's own baseline already carries
BASELINE_DECLINE_PROBABILITY.

These tests fail if a future change to either file breaks the relationship, and
the failure message says which way it broke. They are arithmetic on measured
numbers, not a simulation: they do not need a store, a broker, or a stack.
"""

from __future__ import annotations

import unittest

from detector import config, detect
from surfaces.inject import STAGE_COLLAPSE, STAGE_DEVELOPING

# --- the measured demo cohort ----------------------------------------------
DEMO_PAYMENTS_PER_WINDOW = 170        # measured 157-186; the middle of that
DEMO_BASELINE_CONVERSION = 0.891      # measured 0.890-0.892
DEMO_TRAILING_PAYMENTS = 1960         # measured
# Conversion points lost per unit of injected decline probability, once the
# whole window is inside the deviation: 0.051 / 0.10, measured above.
RESPONSE = 0.51
# What `make verify-demo` allows each stage. Beat 3 waits this long for the
# mild stage to say something; beat 5 measured collapse reaching the board in
# 66 seconds and must not regress.
DEVELOPING_WINDOW_SECONDS = 240
COLLAPSE_BUDGET_SECONDS = 66
WINDOW_SECONDS = config.DETECT_WINDOW_BUCKETS * config.BUCKET_SECONDS


def reading(probability: float, elapsed_seconds: float) -> tuple[float, float]:
    """(absolute drop, z) the injected cohort reads `elapsed_seconds` after a stage.

    The window fills linearly: at `elapsed` seconds only `elapsed / WINDOW_SECONDS`
    of the 5 buckets `evaluate` reads is inside the deviation, so the drop it
    measures is that fraction of the saturated drop.
    """
    filled = min(elapsed_seconds / WINDOW_SECONDS, 1.0)
    drop = RESPONSE * probability * filled
    actual = DEMO_BASELINE_CONVERSION - drop
    z = detect.two_proportion_z(
        actual, DEMO_PAYMENTS_PER_WINDOW, DEMO_BASELINE_CONVERSION, DEMO_TRAILING_PAYMENTS
    )
    return drop, z


def is_incident(drop: float, z: float) -> bool:
    return (
        z <= -config.Z_MIN
        and drop >= config.ABS_DROP_MIN
        and DEMO_PAYMENTS_PER_WINDOW >= config.N_PAYMENTS_MIN
    )


def is_watch(drop: float, z: float) -> bool:
    """The conversion clauses of `watch_floors`.

    Trajectory is not modelled: a deviation that is still growing is worsening
    by construction, and `not_already_an_incident` is asserted separately by
    every caller below.
    """
    return (
        not is_incident(drop, z)
        and z <= config.WATCH_Z_MAX
        and drop >= config.WATCH_ABS_DROP_MIN
        and DEMO_PAYMENTS_PER_WINDOW >= config.N_PAYMENTS_MIN
    )


def first_second(predicate, probability: float, limit: int) -> int | None:
    for elapsed in range(0, limit + 1, 5):
        if predicate(*reading(probability, elapsed)):
            return elapsed
    return None


class TheMildStageSpeaks(unittest.TestCase):
    """A judge presses "Developing deviation" and the board shows a warning."""

    def test_it_is_visible_inside_the_window_the_verifier_allows(self):
        when = first_second(is_watch, STAGE_DEVELOPING, DEVELOPING_WINDOW_SECONDS)
        self.assertIsNotNone(
            when,
            f"STAGE_DEVELOPING={STAGE_DEVELOPING} crosses no watch floor within "
            f"{DEVELOPING_WINDOW_SECONDS}s: the presenter injects a deviation and the "
            f"board stays silent through it. Raise the magnitude or lower "
            f"WATCH_ABS_DROP_MIN / WATCH_Z_MAX - and measure, do not guess.",
        )
        # Not merely inside the window: far enough inside that a quiet minute of
        # live traffic cannot push it out.
        self.assertLessEqual(when, DEVELOPING_WINDOW_SECONDS - 45)

    def test_it_stays_a_warning_and_never_becomes_an_incident(self):
        for elapsed in range(0, 4 * WINDOW_SECONDS, 10):
            drop, z = reading(STAGE_DEVELOPING, elapsed)
            self.assertFalse(
                is_incident(drop, z),
                f"STAGE_DEVELOPING={STAGE_DEVELOPING} clears the detection floors "
                f"{elapsed}s in (drop={drop:.4f}, z={z:.2f}): the mild stage pages "
                f"instead of warning, which is what #77 was opened to fix.",
            )

    def test_the_saturated_reading_keeps_margin_under_the_incident_floor(self):
        _, z = reading(STAGE_DEVELOPING, WINDOW_SECONDS)
        # Live volume moves, and z grows with sqrt(n). A magnitude sitting on the
        # floor becomes an incident on a busy minute.
        self.assertLess(
            abs(z),
            config.Z_MIN * 0.9,
            f"STAGE_DEVELOPING={STAGE_DEVELOPING} saturates at z={z:.2f} against a "
            f"Z_MIN of {config.Z_MIN}: too close to flip to an incident on a busy minute.",
        )


class TheCollapseStageStillPages(unittest.TestCase):
    def test_it_reaches_the_board_within_the_measured_budget(self):
        when = first_second(is_incident, STAGE_COLLAPSE, COLLAPSE_BUDGET_SECONDS)
        self.assertIsNotNone(
            when,
            f"STAGE_COLLAPSE={STAGE_COLLAPSE} does not clear the detection floors "
            f"within {COLLAPSE_BUDGET_SECONDS}s. Collapse reached the board in 66s on "
            f"2026-08-30; a floor was raised too far.",
        )

    def test_it_is_never_merely_watched(self):
        drop, z = reading(STAGE_COLLAPSE, WINDOW_SECONDS)
        self.assertTrue(is_incident(drop, z))


class TheTwoStagesAreDistinguishable(unittest.TestCase):
    def test_the_watch_band_exists_at_all(self):
        """Some magnitude must warn without paging, or the pitch has no warning beat.

        This is the check that would have caught the 0.05 watch floor: with the
        drop floor above the saturated drop of every magnitude that stays under
        Z_MIN, the band is empty and no `developing` value can ever be right.
        """
        band = [
            p / 100
            for p in range(1, 40)
            if is_watch(*reading(p / 100, WINDOW_SECONDS))
        ]
        self.assertTrue(
            band,
            "no injected magnitude produces a watch rather than an incident: the "
            "watch floors and the detection floors leave no band between them.",
        )
        self.assertIn(round(STAGE_DEVELOPING, 2), [round(p, 2) for p in band])
        self.assertLess(max(band), STAGE_COLLAPSE)


if __name__ == "__main__":
    unittest.main()
