"""Cooperative stop for the worker loop.

Python installs a SIGINT handler that raises KeyboardInterrupt, so Ctrl+C
on a host process and `docker kill -s INT` both reach `finally`. Python
does not install a SIGTERM handler. On the host, SIGTERM therefore kills
the process immediately and skips `finally`. Inside a container the worker
is PID 1, and the kernel ignores SIGTERM without a handler; `docker compose
stop` and `timeout` wait, then SIGKILL, and the C6 record is never closed.

This module arms both signals so a clean shutdown still flushes Kafka,
closes the control consumer, and closes the C6 record. A positive duration
stops the same way. A run with no duration stays unbounded.
"""

from __future__ import annotations

import signal
import time
from types import FrameType
from typing import Any


class WorkerStop(Exception):
    """Raised from a signal handler to unwind into the worker's `finally`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RunStopper:
    def __init__(self, duration_seconds: float | None = None) -> None:
        self.reason: str | None = None
        self._deadline: float | None = None
        if duration_seconds is not None and duration_seconds > 0:
            self._deadline = time.monotonic() + duration_seconds

    def arm_signals(self) -> dict[int, Any]:
        previous = {
            signal.SIGINT: signal.getsignal(signal.SIGINT),
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        }
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)
        return previous

    @staticmethod
    def restore(previous: dict[int, Any]) -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        try:
            reason = signal.Signals(signum).name
        except ValueError:
            reason = str(signum)
        raise WorkerStop(reason)

    def duration_elapsed(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def sleep(self, seconds: float) -> None:
        remaining = seconds
        if self._deadline is not None:
            remaining = min(seconds, max(0.0, self._deadline - time.monotonic()))
        if remaining > 0:
            time.sleep(remaining)
