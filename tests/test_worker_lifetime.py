"""Duration and signal shutdown for the worker loop, without Kafka."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from worker.ground_truth import store
from worker.ground_truth.runner import ScenarioRun
from worker.helpers.merchant import Merchant
from worker.runtime import RunStopper


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []
        self.flushed = False
        self.flush_timeout: float | None = None

    def send(self, kind: str, key: str, event: dict) -> None:
        self.sent.append((kind, key, event))

    def flush(self, timeout: float = 10.0) -> None:
        self.flushed = True
        self.flush_timeout = timeout


class FakeControl:
    def __init__(self, incident=None) -> None:
        self.incident = incident
        self.closed = False
        self.polls = 0

    def poll(self) -> None:
        self.polls += 1

    def close(self) -> None:
        self.closed = True


def _closed_record(db_path: Path) -> dict:
    connection = store.connect(db_path)
    try:
        listings = store.list_hidden_truth(connection)
        assert listings, "expected a hidden-truth row"
        record = store.read_hidden_truth(connection, listings[0]["instance_id"])
        assert record is not None
        return {
            "listing": listings[0],
            "record": record,
        }
    finally:
        connection.close()


class DurationHonouredTests(unittest.TestCase):
    def test_scenario_run_exits_at_duration_and_closes_c6(self) -> None:
        from worker.worker import run

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ground_truth.db"
            scenario = ScenarioRun(
                "provider-degradation", duration_seconds=1, db_path=db_path
            )
            producer = FakeProducer()
            control = FakeControl(incident=scenario.incident)
            started = time.monotonic()
            run(
                Merchant("merchant-c"),
                scenario.incident,
                interval_seconds=0.05,
                telemetry_every=0,
                scenario_run=scenario,
                duration_seconds=1,
                producer=producer,
                control=control,
                install_signal_handlers=False,
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.5)
            self.assertGreaterEqual(elapsed, 0.9)
            self.assertTrue(producer.flushed)
            self.assertTrue(control.closed)
            self.assertTrue(scenario._closed)
            closed = _closed_record(db_path)
            self.assertTrue(closed["listing"]["closed"])
            self.assertIn("observed", closed["record"])
            self.assertIsNotNone(closed["listing"]["closed_at"])

    def test_unbounded_run_keeps_going_until_asked_to_stop(self) -> None:
        from worker.worker import run

        stopper = RunStopper(None)
        producer = FakeProducer()
        control = FakeControl()
        finished = {"done": False}

        def _target() -> None:
            run(
                Merchant("merchant-a"),
                None,
                interval_seconds=0.05,
                telemetry_every=0,
                duration_seconds=None,
                producer=producer,
                control=control,
                stopper=stopper,
                install_signal_handlers=False,
            )
            finished["done"] = True

        import threading

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        time.sleep(0.35)
        self.assertTrue(thread.is_alive())
        self.assertFalse(finished["done"])
        stopper._deadline = time.monotonic()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(control.closed)
        self.assertTrue(producer.flushed)


def _armed_worker_script(db_path: Path) -> str:
    return textwrap.dedent(
        f"""
        from pathlib import Path
        from worker.ground_truth.runner import ScenarioRun
        from worker.helpers.merchant import Merchant
        from worker.runtime import RunStopper
        from worker.worker import run
        from tests.test_worker_lifetime import FakeControl, FakeProducer

        class Armed(RunStopper):
            def arm_signals(self):
                previous = super().arm_signals()
                print("ready", flush=True)
                return previous

        db_path = Path({str(db_path)!r})
        scenario = ScenarioRun(
            "provider-degradation", duration_seconds=30, db_path=db_path
        )
        run(
            Merchant("merchant-c"),
            scenario.incident,
            interval_seconds=0.05,
            telemetry_every=0,
            scenario_run=scenario,
            duration_seconds=None,
            producer=FakeProducer(),
            control=FakeControl(incident=scenario.incident),
            stopper=Armed(None),
            install_signal_handlers=True,
        )
        print("exited", flush=True)
        """
    )


class SignalShutdownTests(unittest.TestCase):
    def _start_armed_worker(self, db_path: Path) -> subprocess.Popen:
        """
        Start the armed worker subprocess and wait for it to signal readiness.
        
        Parameters:
        	db_path (Path): Path to the database used by the worker.
        
        Returns:
        	subprocess.Popen: The running worker process after signal handlers are armed.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _armed_worker_script(db_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        ready = ""
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            line = proc.stdout.readline() if proc.stdout is not None else ""
            if line.startswith("ready"):
                ready = line
                break
            if proc.poll() is not None:
                rest = proc.stdout.read() if proc.stdout is not None else ""
                self.fail(f"worker exited before ready: {rest}")
        self.assertTrue(ready.startswith("ready"), ready)
        proc.ready_line = ready  # type: ignore[attr-defined]
        return proc

    def test_sigterm_closes_ground_truth_record(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows subprocesses cannot deliver a catchable SIGTERM")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ground_truth.db"
            proc = self._start_armed_worker(db_path)
            try:
                proc.send_signal(signal.SIGTERM)
                try:
                    out, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                    self.fail(f"SIGTERM did not stop the worker: {out}")
                ready = getattr(proc, "ready_line", "")
                self.assertEqual(proc.returncode, 0, out)
                self.assertIn("stopping on SIGTERM", ready + out)
                self.assertIn("ground truth recorded", ready + out)
                closed = _closed_record(db_path)
                self.assertTrue(closed["listing"]["closed"])
                self.assertIsNotNone(closed["listing"]["closed_at"])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)

    def test_sigint_closes_ground_truth_record(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows subprocesses cannot deliver SIGINT with send_signal")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ground_truth.db"
            proc = self._start_armed_worker(db_path)
            try:
                proc.send_signal(signal.SIGINT)
                try:
                    out, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                    self.fail(f"SIGINT did not stop the worker: {out}")
                ready = getattr(proc, "ready_line", "")
                self.assertEqual(proc.returncode, 0, out)
                self.assertIn("ground truth recorded", ready + out)
                closed = _closed_record(db_path)
                self.assertTrue(closed["listing"]["closed"])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
