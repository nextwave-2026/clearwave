"""The detection service: a loop that does not stop, and cannot see C6.

Two claims are under test here, and they are different in kind.

The first is behavioural and offline: empty polls do not end a run, a stop
request leaves the loop at the top of an iteration, and whatever was already
polled is written and acknowledged before the process exits. Every one of these
runs through the same `Source` seam `tests/test_consumer.py` uses, so no test
here needs a broker.

The second is structural: the detection service must have no read path to
raul's ground truth. `docs/ownership.md` quarantines C6 from W2, W3 and W4, and
`./state:/data` - the mount the investigation service established and the one
the shared store lives behind - contains `state/ground_truth/`. The compose
block therefore has to mask that subpath, and these tests fail if it stops
doing so, if the image gains a copy of `worker/`, or if any detector module
learns to import the ground-truth code.
"""

from __future__ import annotations

import json
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detector import consumer, daemon, store  # noqa: E402
from tests.test_consumer import attempt_records  # noqa: E402
from tests.test_hidden_truth_access import _compose_services  # noqa: E402
from tests import synthetic  # noqa: E402


class QuietSource:
    """A source that never has anything, and counts how often it was asked.

    A topic with no traffic is the normal state of a service at 03:00. The
    one-shot `consume` treats three of these in a row as the end of the run;
    the daemon must not.
    """

    def __init__(self) -> None:
        self.polls = 0
        self.commits = 0
        self.closed = False

    def poll(self, timeout: float):
        self.polls += 1
        return None

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class StopAfterSource(QuietSource):
    """Empty until `stop_after` polls, then sets an event, still returning None."""

    def __init__(self, event: threading.Event, stop_after: int) -> None:
        super().__init__()
        self._event = event
        self._stop_after = stop_after

    def poll(self, timeout: float):
        self.polls += 1
        if self.polls >= self._stop_after:
            self._event.set()
        return None


class RecordsThenQuiet(QuietSource):
    """Hands out records, then goes quiet forever without ever ending the run."""

    def __init__(self, records: list) -> None:
        super().__init__()
        self._records = list(records)

    def poll(self, timeout: float):
        self.polls += 1
        if self._records:
            return self._records.pop(0)
        return None


class ContinuousLoopTests(unittest.TestCase):
    """A quiet topic is not a reason to exit."""

    def setUp(self) -> None:
        self.connection = store.connect(":memory:")

    def tearDown(self) -> None:
        self.connection.close()

    def test_empty_polls_never_end_the_run_when_idle_polls_is_zero(self) -> None:
        stop = threading.Event()
        source = StopAfterSource(stop, stop_after=25)
        consumer.consume(
            self.connection, source, idle_polls=0, should_stop=stop.is_set
        )
        # The one-shot default would have left after three. Only the stop did.
        self.assertGreaterEqual(source.polls, 25)

    def test_the_one_shot_default_still_ends_on_idle(self) -> None:
        """The change must not alter what `detector consume` has always done."""
        source = QuietSource()
        consumer.consume(self.connection, source, idle_polls=3)
        self.assertEqual(source.polls, 3)

    def test_a_stop_request_is_honoured_at_the_top_of_the_loop(self) -> None:
        stop = threading.Event()
        stop.set()
        source = QuietSource()
        consumer.consume(
            self.connection, source, idle_polls=0, should_stop=stop.is_set
        )
        self.assertEqual(source.polls, 0)

    def test_a_stop_drains_the_batch_in_flight_rather_than_dropping_it(self) -> None:
        """Stop mid-batch: the records already polled are stored and acknowledged."""
        events = synthetic.healthy()[:20]
        records = attempt_records(events)
        stop = threading.Event()

        class StopMidBatch(RecordsThenQuiet):
            def poll(inner, timeout: float):  # noqa: N805 - inner self, outer test
                record = super().poll(timeout)
                if inner.polls == len(records):
                    stop.set()
                return record

        # A batch size larger than the run guarantees nothing was flushed by
        # filling a batch: only the drain on the way out can have written these.
        source = StopMidBatch(records)
        progress = consumer.consume(
            self.connection,
            source,
            batch_size=len(records) * 10,
            idle_polls=0,
            should_stop=stop.is_set,
        )
        self.assertEqual(progress.accepted, len(records))
        self.assertEqual(store.stored_counts(self.connection)["attempt"], len(records))
        # Written first, then acknowledged. Never the other way round.
        self.assertEqual(source.commits, 1)

    def test_offsets_are_never_acknowledged_before_the_write_is_durable(self) -> None:
        """The recorded ordering (DECISIONS.md, andres 21:30Z) survives the daemon."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clearwave.db"
            connection = store.connect(path)
            self.addCleanup(connection.close)
            records = attempt_records(synthetic.healthy()[:10])
            stop = threading.Event()
            seen: list[int] = []

            class WatchingSource(RecordsThenQuiet):
                def commit(inner) -> None:  # noqa: N805 - inner self, outer test
                    # Another connection can already see the rows at commit time.
                    import sqlite3

                    observer = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
                    try:
                        seen.append(
                            observer.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
                        )
                    finally:
                        observer.close()
                    stop.set()
                    super().commit()

            consumer.consume(
                connection,
                WatchingSource(records),
                batch_size=len(records),
                idle_polls=0,
                should_stop=stop.is_set,
            )
            self.assertEqual(seen, [len(records)])


class ServeTests(unittest.TestCase):
    """The operator surface around the loop."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "clearwave.db"

    def test_serve_runs_until_stopped_and_closes_the_source(self) -> None:
        stop = threading.Event()
        source = StopAfterSource(stop, stop_after=5)
        code = daemon.serve(
            self.path,
            stop_event=stop,
            source_factory=lambda: source,
            install_signal_handlers=False,
        )
        self.assertEqual(code, 0)
        self.assertTrue(source.closed)

    def test_serve_stores_what_it_consumed(self) -> None:
        records = attempt_records(synthetic.healthy()[:12])
        stop = threading.Event()

        class Feed(RecordsThenQuiet):
            def poll(inner, timeout: float):  # noqa: N805
                record = super().poll(timeout)
                if record is None:
                    stop.set()
                return record

        daemon.serve(
            self.path,
            stop_event=stop,
            source_factory=lambda: Feed(records),
            install_signal_handlers=False,
        )
        connection = store.connect(self.path)
        self.addCleanup(connection.close)
        self.assertEqual(store.stored_counts(connection)["attempt"], len(records))

    def test_resolve_db_prefers_the_flag_then_the_environment(self) -> None:
        self.assertEqual(daemon.resolve_db("/tmp/explicit.db"), Path("/tmp/explicit.db"))
        previous = os.environ.get("CLEARWAVE_DB")
        os.environ["CLEARWAVE_DB"] = "/tmp/from-env.db"
        try:
            self.assertEqual(daemon.resolve_db(None), Path("/tmp/from-env.db"))
        finally:
            if previous is None:
                os.environ.pop("CLEARWAVE_DB", None)
            else:
                os.environ["CLEARWAVE_DB"] = previous
        os.environ.pop("CLEARWAVE_DB", None)
        self.assertEqual(daemon.resolve_db(None), daemon.DEFAULT_DB)
        if previous is not None:
            os.environ["CLEARWAVE_DB"] = previous

    def test_a_bad_bound_is_refused_rather_than_run(self) -> None:
        with self.assertRaises(ValueError):
            daemon.serve(self.path, batch_size=0, install_signal_handlers=False)

    def test_the_daemon_subcommand_exists_on_the_operator_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "detector", "daemon", "--help"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--detect-every", result.stdout)


class SignalTests(unittest.TestCase):
    """SIGINT and SIGTERM both drain. Compose sends SIGTERM; a person sends SIGINT.

    Driven as a real child process, because a handler that works when called
    directly and not when the kernel delivers it is exactly the failure that
    matters: Python installs no SIGTERM handler of its own, and a container's
    PID 1 ignores it without one.
    """

    SCRIPT = textwrap.dedent(
        """
        import sys, threading
        sys.path.insert(0, {root!r})
        from detector import consumer, daemon

        records = []

        class Feed:
            def __init__(self):
                self.polls = 0
            def poll(self, timeout):
                self.polls += 1
                if self.polls <= {count}:
                    return records[self.polls - 1]
                print("READY", flush=True)
                import time; time.sleep(0.05)
                return None
            def commit(self):
                pass
            def close(self):
                pass

        sys.path.insert(0, {root!r})
        from tests.test_consumer import attempt_records
        from tests import synthetic
        records.extend(attempt_records(synthetic.healthy()[:{count}]))

        raise SystemExit(daemon.serve(
            {db!r},
            source_factory=Feed,
            detect_every_seconds=0,
            batch_size=10_000,
        ))
        """
    )

    def _run_and_signal(self, signum: int) -> tuple[str, int]:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "clearwave.db")
            script = self.SCRIPT.format(root=str(ROOT), db=db, count=8)
            process = subprocess.Popen(
                [sys.executable, "-c", script],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    line = process.stdout.readline()
                    if not line or "READY" in line:
                        break
                process.send_signal(signum)
                output = process.communicate(timeout=30)[0]
            finally:
                if process.poll() is None:  # pragma: no cover - only on a hang
                    process.kill()
                    process.communicate()
            connection = store.connect(db)
            try:
                stored = store.stored_counts(connection)["attempt"]
            finally:
                connection.close()
            return output, stored

    def test_sigterm_drains_rather_than_dropping_the_batch(self) -> None:
        output, stored = self._run_and_signal(signal.SIGTERM)
        self.assertIn("stopping on SIGTERM", output)
        self.assertIn("detector daemon stopped", output)
        # A batch size of 10,000 means nothing flushed by filling. All eight
        # records exist only because the stop drained them.
        self.assertEqual(stored, 8)

    def test_sigint_drains_the_same_way(self) -> None:
        output, stored = self._run_and_signal(signal.SIGINT)
        self.assertIn("stopping on SIGINT", output)
        self.assertEqual(stored, 8)


class GroundTruthIsolationTests(unittest.TestCase):
    """The detection service must have no read path to C6, by any route.

    Three routes exist and each is closed here: the compose mount, the image
    contents, and an import. `./state:/data` is the dangerous one - the shared
    store lives under `state/` and so does `state/ground_truth/`, so the mount
    that gives us the store would give us the hidden truth too if the subpath
    were not masked.
    """

    COMPOSE = ROOT / "docker-compose.yml"
    DOCKERFILE = ROOT / "detector/Dockerfile"

    def setUp(self) -> None:
        self.services = _compose_services(self.COMPOSE.read_text(encoding="utf-8"))

    def test_the_detector_service_exists_and_reads_the_shared_store(self) -> None:
        self.assertIn("detector", self.services)
        block = self.services["detector"]
        self.assertIn("CLEARWAVE_DB: /data/clearwave.db", block)
        self.assertIn("./state:/data", block)
        self.assertIn("restart: unless-stopped", block)

    def test_the_ground_truth_subpath_is_masked_over_the_state_mount(self) -> None:
        block = self.services["detector"]
        self.assertIn("tmpfs:", block)
        self.assertIn("- /data/ground_truth", block)

    def test_the_service_is_never_handed_ground_truth_directly(self) -> None:
        block = self.services["detector"]
        self.assertNotIn("CLEARWAVE_GROUND_TRUTH_DB", block)
        self.assertNotIn("state/ground_truth", block)

    def test_the_image_copies_no_tree_that_holds_ground_truth(self) -> None:
        dockerfile = self.DOCKERFILE.read_text(encoding="utf-8")
        copied = [
            line.split()[1]
            for line in dockerfile.splitlines()
            if line.startswith("COPY ")
        ]
        for source in copied:
            self.assertFalse(
                source.startswith(("worker/", "evaluator/", "state/")),
                msg=f"detector image must not copy {source}",
            )

    def test_no_detector_module_imports_the_ground_truth_code(self) -> None:
        import ast

        for path in sorted((ROOT / "detector").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertFalse(
                        name.startswith(("worker", "evaluator")),
                        msg=f"{path.name} imports {name}",
                    )

    def test_a_ground_truth_path_beside_the_store_is_never_opened(self) -> None:
        """The runtime claim: consuming and sweeping touches only the store file.

        Ground truth sits next to the store on the host, so this is the shape
        the container mount masks. Here we prove the code has no interest in it
        even when it is right there - the daemon opens the store it was given
        and nothing else under that directory.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            truth = root / "ground_truth" / "merchant-a"
            truth.mkdir(parents=True)
            secret = truth / "ground_truth.db"
            secret.write_bytes(b"hidden")
            before = secret.stat().st_atime_ns

            stop = threading.Event()
            records = attempt_records(synthetic.with_provider_incident()[:40])

            class Feed(RecordsThenQuiet):
                def poll(inner, timeout: float):  # noqa: N805
                    record = super().poll(timeout)
                    if record is None:
                        stop.set()
                    return record

            opened: list[str] = []
            real_connect = store.connect

            def watching_connect(path, *args, **kwargs):
                opened.append(str(path))
                return real_connect(path, *args, **kwargs)

            store.connect = watching_connect
            try:
                daemon.serve(
                    root / "clearwave.db",
                    stop_event=stop,
                    source_factory=lambda: Feed(records),
                    install_signal_handlers=False,
                    detect_every_seconds=0.0001,
                )
            finally:
                store.connect = real_connect

            for path in opened:
                self.assertNotIn("ground_truth", path)
            self.assertEqual(secret.stat().st_atime_ns, before)
            self.assertEqual(secret.read_bytes(), b"hidden")


if __name__ == "__main__":
    unittest.main()
