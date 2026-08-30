"""Offline round-trip of live incident-control commands, without Kafka."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from worker.helpers.control import IncidentControl
from worker.helpers.incident import INCIDENT_DECLINE_PROBABILITY, Incident
from worker.inject import parse_args, start_command


class _Message:
    def __init__(self, payload: bytes, error=None) -> None:
        self._payload = payload
        self._error = error

    def error(self):
        return self._error

    def value(self):
        return self._payload


class _Consumer:
    def __init__(self, message: _Message | None) -> None:
        self._message = message

    def poll(self, timeout: float = 0):
        return self._message


def _control(
    merchant_id: str,
    command: dict | None,
    incident: Incident | None = None,
) -> IncidentControl:
    control = IncidentControl.__new__(IncidentControl)
    control.merchant_id = merchant_id
    control.incident = incident
    payload = None if command is None else json.dumps(command).encode()
    control._consumer = _Consumer(None if payload is None else _Message(payload))
    return control


class DeclineProbabilityControlTests(unittest.TestCase):
    def test_mild_probability_round_trips_to_incident(self) -> None:
        command = start_command(
            "merchant-b", provider="adyen", decline_probability=0.35
        )
        self.assertEqual(command["decline_probability"], 0.35)
        control = _control("merchant-b", command)
        control.poll()
        self.assertIsNotNone(control.incident)
        self.assertEqual(control.incident.decline_probability, 0.35)

    def test_omitted_probability_falls_back_to_near_total_break(self) -> None:
        command = {
            "merchant_id": "merchant-b",
            "action": "start",
            "scope": {"provider": "adyen"},
            "effect": "decline",
        }
        self.assertNotIn("decline_probability", command)
        control = _control("merchant-b", command)
        control.poll()
        self.assertIsNotNone(control.incident)
        self.assertEqual(
            control.incident.decline_probability, INCIDENT_DECLINE_PROBABILITY
        )
        self.assertEqual(control.incident.decline_probability, 0.95)

    def test_out_of_range_probability_is_rejected_and_keeps_previous_incident(
        self,
    ) -> None:
        previous = Incident(scope={"provider": "adyen"}, decline_probability=0.35)
        command = start_command(
            "merchant-b", provider="adyen", decline_probability=0.35
        )
        command["decline_probability"] = 1.5
        control = _control("merchant-b", command, incident=previous)
        captured = io.StringIO()
        with redirect_stdout(captured):
            control.poll()
        self.assertIs(control.incident, previous)
        self.assertEqual(control.incident.decline_probability, 0.35)
        self.assertIn("incident control: rejected start command:", captured.getvalue())

    def test_incident_rejects_probability_outside_unit_interval(self) -> None:
        with self.assertRaises(ValueError):
            Incident(scope={"provider": "adyen"}, decline_probability=1.5)
        with self.assertRaises(ValueError):
            Incident(scope={"provider": "adyen"}, decline_probability=-0.01)

    def test_cli_parses_decline_probability(self) -> None:
        argv = [
            "worker.inject",
            "merchant-b",
            "--provider",
            "adyen",
            "--decline-probability",
            "0.35",
        ]
        with mock.patch("sys.argv", argv):
            args = parse_args()
        self.assertEqual(args.decline_probability, 0.35)

    def test_cli_defaults_to_near_total_break(self) -> None:
        argv = ["worker.inject", "merchant-b", "--provider", "adyen"]
        with mock.patch("sys.argv", argv):
            args = parse_args()
        self.assertEqual(args.decline_probability, INCIDENT_DECLINE_PROBABILITY)

    def test_cli_rejects_out_of_range_probability(self) -> None:
        argv = [
            "worker.inject",
            "merchant-b",
            "--provider",
            "adyen",
            "--decline-probability",
            "1.5",
        ]
        stderr = io.StringIO()
        with mock.patch("sys.argv", argv), mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit):
                parse_args()
        self.assertIn("0.0", stderr.getvalue())
        self.assertIn("1.0", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
