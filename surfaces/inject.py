"""Adapter for W1's hidden-incident injection.

W4 owns the judge-facing control. W1 owns injection. This module must never
reimplement injection and must never pass a scenario identifier toward
detection or investigation.

TODO: connect to W1's injection entry point once raul publishes it. Until
that function exists, the control reports honestly that injection is not
wired rather than pretending a scenario fired.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

# Candidate callables W1 may publish. None of these paths receive a scenario id.
_CANDIDATES = (
    ("simulator.inject", "fire_hidden_incident"),
    ("world.inject", "fire_hidden_incident"),
    ("w1.inject", "fire_hidden_incident"),
)


def fire_hidden_incident(loader: Callable[[], Callable[[], Any] | None] | None = None) -> dict[str, Any]:
    """Fire a hidden incident through W1, or report that injection is not wired."""
    resolve = loader or load_injector
    injector = resolve()
    if injector is None:
        return {
            "wired": False,
            "fired": False,
            "message": "injection is not wired",
        }
    result = injector()
    return {"wired": True, "fired": True, "result": result}


def load_injector() -> Callable[[], Any] | None:
    """Return W1's zero-argument injector, or None if it has not landed."""
    for module_name, attribute in _CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        candidate = getattr(module, attribute, None)
        if callable(candidate):
            return candidate
    return None
