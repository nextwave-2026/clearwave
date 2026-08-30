"""Continuous investigation process.

``InvestigationRunner.run_forever`` already polls, claims, investigates, and
drains. This module is the operator surface around that loop: load the root
``.env``, parse arguments, arm SIGINT and SIGTERM, and close the runner.

Signal handling mirrors ``worker.runtime`` (Python has no SIGTERM handler, and
a container PID 1 ignores SIGTERM without one) but does not import it. The
worker stopper raises from the handler to unwind a Kafka/C6 ``finally``;
this process sets the runner's ``threading.Event`` so ``run_forever`` can
drain in-flight work and persist a complete result.
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path
from types import FrameType
from typing import Any

from .agent import InvestigationAgent
from .env import api_key_present, load_dotenv
from .runner import InvestigationRunner
from .store import connect as investigation_connect
from .vertical import UnavailableClient

DEFAULT_DB = Path("state/clearwave.db")
DEFAULT_POLL_INTERVAL_SECONDS = 2.0


def build_agent() -> InvestigationAgent:
    """Construct the agent. Without a key, never build the OpenAI client."""
    if api_key_present():
        return InvestigationAgent()
    return InvestigationAgent(client=UnavailableClient())


def resolve_db(path: str | None) -> Path:
    """Store path: ``--db``, else ``CLEARWAVE_DB``, else ``state/clearwave.db``."""
    if path:
        return Path(path)
    env = os.environ.get("CLEARWAVE_DB")
    if env:
        return Path(env)
    return Path(DEFAULT_DB)


def arm_stop(event: threading.Event, reason_holder: dict[str, str]) -> dict[int, Any]:
    """Install SIGINT and SIGTERM handlers that set ``event`` instead of raising."""
    previous = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }

    def handle(signum: int, _frame: FrameType | None) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        reason_holder["reason"] = name
        event.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)
    return previous


def restore_signals(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def serve(
    db_path: Path | str,
    *,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_polls: int | None = None,
    stop_event: threading.Event | None = None,
    agent: Any | None = None,
    install_signal_handlers: bool = True,
) -> int:
    """Run the investigation loop until stopped or ``max_polls`` is reached."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll interval must be positive")
    if max_polls is not None and max_polls < 0:
        raise ValueError("max-polls must be zero or positive")

    resolved = Path(db_path).resolve()
    os.environ["CLEARWAVE_DB"] = str(resolved)
    connection = investigation_connect(resolved)
    runner = InvestigationRunner(
        connection,
        agent if agent is not None else build_agent(),
        poll_interval_seconds=poll_interval_seconds,
    )
    event = stop_event or threading.Event()
    reason_holder: dict[str, str] = {}
    previous: dict[int, Any] | None = None
    if install_signal_handlers:
        previous = arm_stop(event, reason_holder)
    try:
        print(
            f"investigation daemon watching {resolved} interval={poll_interval_seconds}s",
            flush=True,
        )
        runner.run_forever(event, max_polls=max_polls)
        if reason_holder.get("reason"):
            print(
                f"investigation daemon stopping on {reason_holder['reason']}",
                flush=True,
            )
        return 0
    finally:
        if previous is not None:
            restore_signals(previous)
        runner.close()
        connection.close()
        print("investigation daemon stopped", flush=True)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="investigation",
        description="Watch the shared store and investigate detected incidents.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite store path (default: $CLEARWAVE_DB or state/clearwave.db)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"seconds between polls (default: {DEFAULT_POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--max-polls",
        type=int,
        default=None,
        help="stop after this many polls (default: run until SIGINT or SIGTERM)",
    )
    args = parser.parse_args(argv)
    try:
        return serve(
            resolve_db(args.db),
            poll_interval_seconds=args.poll_interval,
            max_polls=args.max_polls,
        )
    except ValueError as exc:
        print(f"investigation daemon: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
