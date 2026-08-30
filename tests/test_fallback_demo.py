"""The staged fallback is deterministic input to the real detector."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import fallback_demo  # noqa: E402


class FallbackInputTests(unittest.TestCase):
    def test_staged_attempts_repeat_for_the_same_anchor(self):
        anchor = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        first = fallback_demo.staged_events(anchor)
        second = fallback_demo.staged_events(anchor)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 240)
        self.assertEqual({event["merchant_id"] for event in first}, {"merchant-b"})
        self.assertEqual({event["provider"] for event in first}, {"adyen"})
        self.assertEqual({event["payment_method"] for event in first}, {"pse"})

    def test_staged_attempts_are_real_canonical_outcomes(self):
        events = fallback_demo.staged_events(
            datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        )
        self.assertEqual({event["status"] for event in events}, {"approved", "declined"})
        self.assertEqual(
            sum(event["status"] == "declined" for event in events),
            49,
        )
        self.assertTrue(all(event["normalized_decline_reason"] == "timeout" for event in events if event["status"] == "declined"))
        self.assertTrue(all("normalized_decline_reason" not in event for event in events if event["status"] == "approved"))
        self.assertEqual(len({event["event_id"] for event in events}), len(events))
        self.assertEqual(len({event["payment_id"] for event in events}), len(events))
        self.assertEqual(len({event["attempt_id"] for event in events}), len(events))


if __name__ == "__main__":
    unittest.main()
