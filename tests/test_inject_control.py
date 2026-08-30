"""decline_probability across the control topic, end to end without a broker.

The magnitude of a decline was tunable on the Incident object but the
control topic dropped it, so a live inject could only ever fire the
near-total break. That made a mild deviation impossible to inject, which is
what the early-warning demo beat needs before the hard collapse.

Two properties matter more than the plumbing and are asserted here rather
than assumed: an omitted field still means the hard default, so the judge
toggle cannot be silently weakened by this change; and a percentage typed
where a ratio belongs is refused at the command builder as well as at the
worker, so nobody publishes a command that is discarded into a log.
"""

from __future__ import annotations

import json
import unittest

from worker.helpers.control import IncidentControl
from worker.helpers.incident import (
    DECLINE,
    INCIDENT_DECLINE_PROBABILITY,
    LATENCY,
    Incident,
)
from worker.inject import start_command, stop_command


class FakeMessage:
    """One control-topic message, in the shape confluent_kafka hands back."""

    def __init__(self, command: dict | bytes) -> None:
        self._value = command if isinstance(command, bytes) else json.dumps(command).encode()

    def error(self):
        return None

    def value(self) -> bytes:
        return self._value


class FakeConsumer:
    """Replays queued messages one poll at a time, then returns None."""

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)
        self.closed = False

    def poll(self, timeout: float = 0):
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


def control_for(commands: list, merchant_id: str = "merchant-b") -> IncidentControl:
    return IncidentControl(
        merchant_id, consumer=FakeConsumer([FakeMessage(c) for c in commands])
    )


class StartCommandTests(unittest.TestCase):
    def test_the_command_carries_the_requested_probability(self):
        command = start_command("merchant-b", provider="adyen", decline_probability=0.35)
        self.assertEqual(command["decline_probability"], 0.35)

    def test_omitting_it_still_fires_the_near_total_break(self):
        # The judge toggle calls start_command without this argument. It must
        # keep meaning exactly what it meant before the field existed.
        command = start_command("merchant-b", provider="adyen")
        self.assertEqual(command["decline_probability"], INCIDENT_DECLINE_PROBABILITY)
        self.assertEqual(command["decline_probability"], 0.95)

    def test_a_percentage_is_refused_at_the_terminal(self):
        with self.assertRaises(ValueError) as raised:
            start_command("merchant-b", provider="adyen", decline_probability=35)
        self.assertIn("ratio, not a percentage", str(raised.exception))

    def test_a_negative_probability_is_refused(self):
        with self.assertRaises(ValueError):
            start_command("merchant-b", provider="adyen", decline_probability=-0.1)

    def test_a_non_number_is_refused(self):
        with self.assertRaises(ValueError):
            start_command("merchant-b", provider="adyen", decline_probability="mild")

    def test_the_bounds_are_inclusive(self):
        for probability in (0.0, 1.0):
            command = start_command(
                "merchant-b", provider="adyen", decline_probability=probability
            )
            self.assertEqual(command["decline_probability"], probability)

    def test_it_still_carries_no_scenario_identifier(self):
        # C6 quarantine: a magnitude is not a scenario name. Adding this field
        # must not become a channel for the hidden truth.
        rendered = json.dumps(
            start_command("merchant-b", provider="adyen", decline_probability=0.35)
        ).lower()
        self.assertNotIn("scenario", rendered)
        self.assertNotIn("ground_truth", rendered)


class WorkerHonoursTheCommandTests(unittest.TestCase):
    def test_the_worker_applies_the_requested_probability(self):
        control = control_for([start_command("merchant-b", provider="adyen", decline_probability=0.35)])
        control.poll()
        self.assertEqual(control.incident.decline_probability, 0.35)
        self.assertEqual(control.incident.scope, {"provider": "adyen"})
        self.assertEqual(control.incident.effect, DECLINE)

    def test_a_command_without_the_field_still_breaks_hard(self):
        # A command published by an older build, or by the judge toggle.
        control = control_for(
            [{"merchant_id": "merchant-b", "action": "start", "scope": {"provider": "adyen"}}]
        )
        control.poll()
        self.assertEqual(control.incident.decline_probability, INCIDENT_DECLINE_PROBABILITY)

    def test_mild_then_hard_replaces_the_incident_in_place(self):
        # The demo beat: a mild deviation the detector watches, then the
        # collapse it detects, with no worker restart between them.
        control = control_for(
            [
                start_command("merchant-b", provider="adyen", decline_probability=0.35),
                start_command("merchant-b", provider="adyen", decline_probability=0.95),
            ]
        )
        control.poll()
        self.assertEqual(control.incident.decline_probability, 0.35)
        control.poll()
        self.assertEqual(control.incident.decline_probability, 0.95)
        self.assertEqual(control.incident.scope, {"provider": "adyen"})

    def test_an_out_of_range_probability_on_the_wire_is_rejected(self):
        # Rejected, and the previous incident is left standing rather than
        # cleared - a malformed command must not silently end an injection.
        standing = Incident(scope={"provider": "adyen"}, decline_probability=0.35)
        control = IncidentControl(
            "merchant-b",
            initial=standing,
            consumer=FakeConsumer(
                [
                    FakeMessage(
                        {
                            "merchant_id": "merchant-b",
                            "action": "start",
                            "scope": {"provider": "adyen"},
                            "decline_probability": 35,
                        }
                    )
                ]
            ),
        )
        control.poll()
        self.assertIs(control.incident, standing)

    def test_a_command_for_another_merchant_is_ignored(self):
        control = control_for(
            [start_command("merchant-c", provider="adyen", decline_probability=0.1)]
        )
        control.poll()
        self.assertIsNone(control.incident)

    def test_stop_still_clears_the_incident(self):
        control = control_for(
            [
                start_command("merchant-b", provider="adyen", decline_probability=0.35),
                stop_command("merchant-b"),
            ]
        )
        control.poll()
        self.assertIsNotNone(control.incident)
        control.poll()
        self.assertIsNone(control.incident)


class IncidentValidationTests(unittest.TestCase):
    def test_the_dataclass_refuses_an_impossible_probability(self):
        with self.assertRaises(ValueError):
            Incident(scope={"provider": "adyen"}, decline_probability=1.5)

    def test_a_boolean_is_not_a_probability(self):
        # True == 1 in Python, so an unguarded range check would accept it.
        with self.assertRaises(ValueError):
            Incident(scope={"provider": "adyen"}, decline_probability=True)

    def test_the_scenario_magnitudes_all_remain_legal(self):
        # worker/ground_truth/scenarios.py pins 0.55, 0.85 and 0.22.
        from worker.ground_truth.scenarios import SCENARIOS

        for definition in SCENARIOS.values():
            incident = definition.build_incident()
            self.assertGreaterEqual(incident.decline_probability, 0.0)
            self.assertLessEqual(incident.decline_probability, 1.0)

    def test_a_non_decline_effect_is_unaffected_by_the_probability(self):
        incident = Incident(scope={"provider": "adyen"}, effect=LATENCY)
        self.assertEqual(incident.decline_probability, INCIDENT_DECLINE_PROBABILITY)


if __name__ == "__main__":
    unittest.main()
