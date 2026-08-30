"""Operator surface and process lifecycle for the investigation daemon."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from investigation.agent import InvestigationAgent
from investigation.daemon import (
    DEFAULT_DB,
    DEFAULT_POLL_INTERVAL_SECONDS,
    build_agent,
    main,
    resolve_db,
    serve,
)
from investigation.degrade import degrade_result
from investigation.store import connect, insert_incident
from investigation.vertical import UnavailableClient
from surfaces.escalation import SLACK_ENV, TWILIO_ENV_VARS
from surfaces.store import connect as surfaces_connect
from surfaces.store import ensure_escalation, load_investigation


ROOT = Path(__file__).resolve().parents[1]
INCIDENT = {
    "incident_id": "inc-daemon-1",
    "affected_cohort": {
        "merchant_id": "merchant-b",
        "provider": "adyen",
        "country": "CO",
    },
    "change": {
        "metric": "payment_approval_conversion",
        "expected": 0.92,
        "actual": 0.11,
        "absolute_delta": -0.81,
    },
    "onset": "2026-08-30T10:00:00Z",
    "persistence": {
        "is_persistent": True,
        "observed_for_seconds": 420,
        "last_observed_at": "2026-08-30T10:07:00Z",
    },
    "blast_radius": {
        "attempted_payments": 800,
        "affected_merchants": 1,
        "affected_countries": 1,
        "affected_providers": 1,
    },
    "financial_impact": {
        "gmv_at_risk": {"amount": 4800, "currency": "USD"},
        "loss_per_hour": {"amount": 57000, "currency": "USD"},
    },
    "severity": "high",
    "lifecycle_state": "detected",
}


class CountingAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def investigate(self, incident):
        incident_id = str(incident["incident_id"])
        self.calls.append(incident_id)
        return degrade_result(incident, reason="counting agent")


class SlowAgent:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.started = threading.Event()
        self.calls: list[str] = []

    def investigate(self, incident):
        self.started.set()
        time.sleep(self.delay)
        incident_id = str(incident["incident_id"])
        self.calls.append(incident_id)
        return degrade_result(incident, reason="slow agent")


def _blank_escalation_env() -> dict[str, str | None]:
    saved: dict[str, str | None] = {}
    for key in (SLACK_ENV, *TWILIO_ENV_VARS, "CLEARWAVE_TWILIO_AUTH_TOKEN"):
        saved[key] = os.environ.pop(key, None)
        os.environ[key] = ""
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _lifecycle(db_path: Path, incident_id: str) -> str:
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT lifecycle_state FROM incident WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        return str(row["lifecycle_state"] if row is not None else "")
    finally:
        connection.close()


def _result_count(db_path: Path, incident_id: str | None = None) -> int:
    connection = connect(db_path)
    try:
        if incident_id is None:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM investigation_result"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM investigation_result WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return int(row["n"])
    finally:
        connection.close()


class ResolveDbTests(unittest.TestCase):
    def test_flag_wins_then_env_then_shared_default(self) -> None:
        self.assertEqual(resolve_db("/tmp/explicit.db"), Path("/tmp/explicit.db"))
        saved = os.environ.get("CLEARWAVE_DB")
        os.environ["CLEARWAVE_DB"] = "/tmp/from-env.db"
        try:
            self.assertEqual(resolve_db(None), Path("/tmp/from-env.db"))
        finally:
            if saved is None:
                os.environ.pop("CLEARWAVE_DB", None)
            else:
                os.environ["CLEARWAVE_DB"] = saved
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLEARWAVE_DB", None)
            self.assertEqual(resolve_db(None), Path(DEFAULT_DB))


class BuildAgentTests(unittest.TestCase):
    def test_missing_key_does_not_construct_openai(self) -> None:
        saved = os.environ.pop("OPENAI_API_KEY", None)
        try:
            agent = build_agent()
            self.assertIsInstance(agent.client, UnavailableClient)
        finally:
            if saved is not None:
                os.environ["OPENAI_API_KEY"] = saved


class DaemonServeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "clearwave.db"
        self._saved_db = os.environ.get("CLEARWAVE_DB")
        self._escalation = _blank_escalation_env()
        self.addCleanup(lambda: _restore_env(self._escalation))

    def tearDown(self) -> None:
        if self._saved_db is None:
            os.environ.pop("CLEARWAVE_DB", None)
        else:
            os.environ["CLEARWAVE_DB"] = self._saved_db

    def test_diagnoses_a_detected_incident_without_a_human_command(self) -> None:
        connection = connect(self.db)
        insert_incident(connection, INCIDENT)
        connection.close()
        agent = CountingAgent()
        code = serve(
            self.db,
            poll_interval_seconds=0.05,
            max_polls=3,
            agent=agent,
            install_signal_handlers=False,
        )
        self.assertEqual(code, 0)
        self.assertEqual(agent.calls, ["inc-daemon-1"])
        self.assertEqual(_lifecycle(self.db, "inc-daemon-1"), "diagnosed")
        self.assertEqual(_result_count(self.db, "inc-daemon-1"), 1)

    def test_idle_store_does_not_call_the_agent(self) -> None:
        connect(self.db).close()
        agent = CountingAgent()
        started = time.monotonic()
        code = serve(
            self.db,
            poll_interval_seconds=0.05,
            max_polls=4,
            agent=agent,
            install_signal_handlers=False,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0)
        self.assertEqual(agent.calls, [])
        self.assertEqual(_result_count(self.db), 0)
        self.assertLess(elapsed, 2.0)

    def test_points_c2_tools_at_the_same_store(self) -> None:
        serve(
            self.db,
            poll_interval_seconds=0.01,
            max_polls=1,
            agent=CountingAgent(),
            install_signal_handlers=False,
        )
        self.assertEqual(Path(os.environ["CLEARWAVE_DB"]), self.db.resolve())

    def test_stop_event_drains_an_in_flight_investigation(self) -> None:
        connection = connect(self.db)
        insert_incident(connection, INCIDENT)
        connection.close()
        agent = SlowAgent(0.4)
        stop = threading.Event()

        def _run() -> None:
            serve(
                self.db,
                poll_interval_seconds=0.05,
                agent=agent,
                stop_event=stop,
                install_signal_handlers=False,
            )

        thread = threading.Thread(target=_run)
        thread.start()
        self.assertTrue(agent.started.wait(timeout=2))
        stop.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(_lifecycle(self.db, "inc-daemon-1"), "diagnosed")
        self.assertEqual(_result_count(self.db, "inc-daemon-1"), 1)
        self.assertNotEqual(_lifecycle(self.db, "inc-daemon-1"), "investigating")

    def test_dashboard_read_after_daemon_diagnosis_fires_one_complete_notification(
        self,
    ) -> None:
        connection = connect(self.db)
        insert_incident(connection, INCIDENT)
        connection.close()
        agent = InvestigationAgent(client=UnavailableClient())
        serve(
            self.db,
            poll_interval_seconds=0.05,
            max_polls=2,
            agent=agent,
            install_signal_handlers=False,
        )
        self.assertEqual(_lifecycle(self.db, "inc-daemon-1"), "diagnosed")

        connection = surfaces_connect(self.db)
        try:
            incident = dict(INCIDENT)
            incident["lifecycle_state"] = "diagnosed"
            stored = load_investigation(connection, "inc-daemon-1")
            self.assertIsNotNone(stored)
            events = ensure_escalation(connection, incident, stored)
            again = ensure_escalation(connection, incident, stored)
        finally:
            connection.close()

        self.assertEqual({event["channel"] for event in events}, {"dashboard", "slack", "phone"})
        self.assertEqual(len(events), 3)
        self.assertEqual(len(again), 3)
        self.assertEqual(
            [(event["channel"], event["status"]) for event in events],
            [(event["channel"], event["status"]) for event in again],
        )
        payloads = [event["payload"] for event in events]
        for payload in payloads:
            # leading_hypothesis / diagnostic_confidence / recommended_next_action are None
            # (not placeholder text) when outcome=agent_unavailable; see
            # docs/contracts/notification-escalation.md
            self.assertIsNone(payload.get("leading_hypothesis"))
            self.assertIsNone(payload.get("diagnostic_confidence"))
            self.assertIsNone(payload.get("recommended_next_action"))
            self.assertTrue(payload.get("citations"))
        slack = next(event for event in events if event["channel"] == "slack")
        self.assertEqual(slack["status"], "not_configured")


class ConcurrentClaimTests(unittest.TestCase):
    def test_two_runners_cannot_claim_the_same_incident(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clearwave.db"
            writer = connect(db)
            insert_incident(writer, INCIDENT)
            writer.close()

            barrier = threading.Barrier(2)
            collected: list[list[str]] = []
            lock = threading.Lock()

            def _claim() -> None:
                connection = connect(db)
                agent = SlowAgent(0.25)
                from investigation.runner import InvestigationRunner

                runner = InvestigationRunner(
                    connection, agent, poll_interval_seconds=0.05
                )
                barrier.wait()
                try:
                    runs = runner.poll_once(wait=True)
                    with lock:
                        collected.append([run.result.incident_id for run in runs])
                finally:
                    runner.close()
                    connection.close()

            threads = [threading.Thread(target=_claim) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            claimed = [item for batch in collected for item in batch]
            self.assertEqual(claimed, ["inc-daemon-1"])
            self.assertEqual(_lifecycle(db, "inc-daemon-1"), "diagnosed")
            self.assertEqual(_result_count(db, "inc-daemon-1"), 1)

    def test_daemon_and_oneshot_cannot_double_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clearwave.db"
            writer = connect(db)
            insert_incident(writer, INCIDENT)
            writer.close()

            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["CLEARWAVE_DB"] = str(db)
            env["CLEARWAVE_SLACK_WEBHOOK_URL"] = ""
            env["CLEARWAVE_TWILIO_ACCOUNT_SID"] = ""
            env["CLEARWAVE_TWILIO_AUTH_TOKEN"] = ""
            env.pop("OPENAI_API_KEY", None)

            daemon = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    textwrap.dedent(
                        f"""
                        from pathlib import Path
                        from investigation.daemon import serve
                        from tests.test_investigation_daemon import SlowAgent
                        serve(
                            Path({str(db)!r}),
                            poll_interval_seconds=0.05,
                            max_polls=20,
                            agent=SlowAgent(0.4),
                            install_signal_handlers=False,
                        )
                        """
                    ),
                ],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            oneshot = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    textwrap.dedent(
                        f"""
                        from pathlib import Path
                        from investigation.daemon import serve
                        from tests.test_investigation_daemon import SlowAgent
                        serve(
                            Path({str(db)!r}),
                            poll_interval_seconds=0.05,
                            max_polls=20,
                            agent=SlowAgent(0.4),
                            install_signal_handlers=False,
                        )
                        """
                    ),
                ],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                daemon_out, _ = daemon.communicate(timeout=8)
                oneshot_out, _ = oneshot.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                daemon.kill()
                oneshot.kill()
                daemon.communicate()
                oneshot.communicate()
                self.fail("concurrent claim processes did not exit")
            self.assertEqual(daemon.returncode, 0, daemon_out)
            self.assertEqual(oneshot.returncode, 0, oneshot_out)
            self.assertEqual(_lifecycle(db, "inc-daemon-1"), "diagnosed")
            self.assertEqual(_result_count(db, "inc-daemon-1"), 1)


class CliTests(unittest.TestCase):
    def test_main_loads_dotenv_before_building_the_agent(self) -> None:
        order: list[str] = []

        def fake_load() -> dict[str, str]:
            order.append("dotenv")
            return {}

        def fake_serve(*args, **kwargs) -> int:
            order.append("serve")
            return 0

        with mock.patch("investigation.daemon.load_dotenv", side_effect=fake_load):
            with mock.patch("investigation.daemon.serve", side_effect=fake_serve):
                code = main(["--db", "/tmp/unused-daemon.db", "--max-polls", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(order, ["dotenv", "serve"])

    def test_help_and_defaults(self) -> None:
        with mock.patch("sys.stdout"):
            with self.assertRaises(SystemExit) as raised:
                main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(DEFAULT_POLL_INTERVAL_SECONDS, 2.0)
        self.assertEqual(DEFAULT_DB.as_posix(), "state/clearwave.db")

    def test_rejects_a_non_positive_interval(self) -> None:
        with mock.patch("sys.stdout"):
            code = main(["--db", "/tmp/unused-daemon.db", "--poll-interval", "0"])
        self.assertEqual(code, 2)


def _armed_daemon_script(db_path: Path, delay: float) -> str:
    return textwrap.dedent(
        f"""
        from pathlib import Path
        from investigation.daemon import serve
        from investigation.store import connect, insert_incident
        from tests.test_investigation_daemon import INCIDENT, SlowAgent

        db = Path({str(db_path)!r})
        connection = connect(db)
        insert_incident(connection, INCIDENT)
        connection.close()
        serve(
            db,
            poll_interval_seconds=0.05,
            agent=SlowAgent({delay}),
            install_signal_handlers=True,
        )
        """
    )


def _armed_idle_script(db_path: Path) -> str:
    return textwrap.dedent(
        f"""
        from pathlib import Path
        from investigation.daemon import serve
        from tests.test_investigation_daemon import CountingAgent

        serve(
            Path({str(db_path)!r}),
            poll_interval_seconds=0.05,
            agent=CountingAgent(),
            install_signal_handlers=True,
        )
        """
    )


class SignalShutdownTests(unittest.TestCase):
    def _start(self, script: str) -> subprocess.Popen:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["CLEARWAVE_SLACK_WEBHOOK_URL"] = ""
        env["CLEARWAVE_TWILIO_ACCOUNT_SID"] = ""
        env["CLEARWAVE_TWILIO_AUTH_TOKEN"] = ""
        env.pop("OPENAI_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        deadline = time.monotonic() + 8
        ready = ""
        while time.monotonic() < deadline:
            line = proc.stdout.readline() if proc.stdout is not None else ""
            if "watching" in line:
                ready = line
                break
            if proc.poll() is not None:
                rest = proc.stdout.read() if proc.stdout is not None else ""
                self.fail(f"daemon exited before ready: {rest}")
        self.assertTrue(ready, "daemon never printed ready")
        proc.ready_line = ready  # type: ignore[attr-defined]
        return proc

    def _stop(self, proc: subprocess.Popen, signum: int, label: str) -> str:
        try:
            time.sleep(0.2)
            proc.send_signal(signum)
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
                self.fail(f"{label} did not stop the daemon: {out}")
            ready = getattr(proc, "ready_line", "")
            self.assertEqual(proc.returncode, 0, ready + out)
            return ready + out
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)

    def test_sigterm_drains_and_persists(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows subprocesses cannot deliver a catchable SIGTERM")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clearwave.db"
            proc = self._start(_armed_daemon_script(db, 0.5))
            out = self._stop(proc, signal.SIGTERM, "SIGTERM")
            self.assertIn("stopping on SIGTERM", out)
            self.assertIn("stopped", out)
            self.assertEqual(_lifecycle(db, "inc-daemon-1"), "diagnosed")
            self.assertEqual(_result_count(db, "inc-daemon-1"), 1)
            self.assertNotEqual(_lifecycle(db, "inc-daemon-1"), "investigating")

    def test_sigint_drains_and_persists(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows subprocesses cannot deliver SIGINT with send_signal")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clearwave.db"
            proc = self._start(_armed_daemon_script(db, 0.5))
            out = self._stop(proc, signal.SIGINT, "SIGINT")
            self.assertIn("stopped", out)
            self.assertEqual(_lifecycle(db, "inc-daemon-1"), "diagnosed")
            self.assertEqual(_result_count(db, "inc-daemon-1"), 1)

    def test_sigterm_on_idle_store_leaves_nothing_behind(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows subprocesses cannot deliver a catchable SIGTERM")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "clearwave.db"
            proc = self._start(_armed_idle_script(db))
            out = self._stop(proc, signal.SIGTERM, "SIGTERM")
            self.assertIn("stopping on SIGTERM", out)
            self.assertEqual(_result_count(db), 0)


class IsolationFromHiddenTruthTests(unittest.TestCase):
    def test_daemon_module_does_not_read_ground_truth_env(self) -> None:
        source = (ROOT / "investigation" / "daemon.py").read_text(encoding="utf-8")
        self.assertNotIn("CLEARWAVE_GROUND_TRUTH_DB", source)
        self.assertNotIn("ground_truth", source)
        main_source = (ROOT / "investigation" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("CLEARWAVE_GROUND_TRUTH_DB", main_source)


if __name__ == "__main__":
    unittest.main()
