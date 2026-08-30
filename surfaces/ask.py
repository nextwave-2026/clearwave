"""The board's one seam onto the ask engine.

W4 holds no domain logic. Nothing here runs a query, reads a metric or decides
an answer: this module calls `investigation.ask.ask` once and normalises what
comes back into the shape the panel renders. Every figure the panel shows
arrives from that call already tied to the query that produced it.

The engine is imported lazily and by name so the board, and its tests, run
without it. `_call_engine` is the only place it is invoked.

Two vocabularies meet here and must not be confused:

* `outcome` is the engine's, unchanged - `diagnosed`, `ambiguous`,
  `insufficient_evidence`, `agent_unavailable`. All four are answers. A
  question the tools cannot reach is not an error.
* `unavailable_kind` is this layer's own, and only ever set alongside
  `agent_unavailable`. It says *why* the agent was not available, because "no
  key configured" and "it ran past its limit" are different things to a judge
  and one word for both would hide that. The engine's own `reason` is free
  prose and is passed through beside it, never parsed for meaning.

The interactive box asks for a shorter deadline than the engine's own 60s
default: a judge is watching a dashboard, and half a minute of nothing is
already a long time to stand in front of one.
"""

from __future__ import annotations

import concurrent.futures
import datetime as _datetime
import importlib
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from investigation.env import api_key_present

ENGINE_MODULE = "investigation.ask"
ENGINE_ATTR = "ask"

# The engine's outcome vocabulary, not a second one invented for this panel.
OUTCOMES = ("diagnosed", "ambiguous", "insufficient_evidence", "agent_unavailable")
ANSWERED = "diagnosed"
UNAVAILABLE = "agent_unavailable"

# Why the agent was unavailable. Only ever set alongside `agent_unavailable`.
KIND_NO_API_KEY = "no_api_key"
KIND_ENGINE_MISSING = "engine_missing"
KIND_TIMEOUT = "timeout"
KIND_ENGINE_ERROR = "engine_error"

MAX_QUESTION_CHARS = 400

# What the panel asks the engine for. The engine bounds itself at this; the
# outer guard below is a backstop for the case where it somehow does not.
ASK_TIMEOUT_SECONDS = 30.0
_GUARD_MARGIN_SECONDS = 5.0

# The engine names a deadline in its own prose. Matching on it only chooses
# which of two honest wordings to draw - no figure or outcome depends on it.
_DEADLINE_HINTS = ("deadline", "timed out", "timeout")

# Sentinel: by default an adapter-raised unavailable shows its own detail as the
# card body. States whose panel wording already says it pass answer=None.
_SAME_AS_REASON = object()


def engine() -> Callable[..., Any] | None:
    """The landed entry point, or None where the engine is not installed."""
    try:
        module = importlib.import_module(ENGINE_MODULE)
    except ModuleNotFoundError:
        return None
    entry = getattr(module, ENGINE_ATTR, None)
    return entry if callable(entry) else None


def answer(
    question: str,
    db_path: Any,
    *,
    timeout: float = ASK_TIMEOUT_SECONDS,
    entry_point: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Ask one question and return the panel's payload. Never raises."""
    text = (question or "").strip()
    if not text:
        return _unavailable("", KIND_ENGINE_ERROR, "No question was asked.")
    if len(text) > MAX_QUESTION_CHARS:
        text = text[:MAX_QUESTION_CHARS]
    if not api_key_present():
        # No `answer` prose: the panel's own wording for this state already says
        # it, and printing both would say the same thing twice on one card.
        return _unavailable(text, KIND_NO_API_KEY, "OPENAI_API_KEY is not set.", answer=None)
    call = entry_point if entry_point is not None else engine()
    if call is None:
        return _unavailable(
            text,
            KIND_ENGINE_MISSING,
            f"{ENGINE_MODULE} is not importable in this build.",
            answer=None,
        )
    return _call_engine(call, text, db_path, timeout)


def _call_engine(
    call: Callable[..., Any],
    question: str,
    db_path: Any,
    timeout: float,
) -> dict[str, Any]:
    """The one place the engine is invoked. Swapping engines moves only this."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_invoke, call, question, db_path, timeout)
        try:
            payload = future.result(timeout=timeout + _GUARD_MARGIN_SECONDS)
        except concurrent.futures.TimeoutError:
            # The worker finishes on its own; the caller's lock is what stops a
            # second question stacking behind a slow one.
            pool.shutdown(wait=False, cancel_futures=True)
            return _unavailable(
                question,
                KIND_TIMEOUT,
                f"The question ran past its {int(timeout)} second limit and was not answered.",
            )
        except Exception as error:  # noqa: BLE001 - a failed agent is an outcome, not a crash
            return _unavailable(question, KIND_ENGINE_ERROR, _describe(error))
    return normalise(question, payload)


def _invoke(call: Callable[..., Any], question: str, db_path: Any, timeout: float) -> Any:
    """Ask for the shorter deadline, tolerating an engine that has no such knob."""
    try:
        return call(question, db_path, timeout_seconds=timeout)
    except TypeError:
        return call(question, db_path)


def normalise(question: str, payload: Any) -> dict[str, Any]:
    """Shape what the engine returned into the panel's contract."""
    data = payload if isinstance(payload, Mapping) else {}
    outcome = str(data.get("outcome") or ANSWERED)
    if outcome not in OUTCOMES:
        outcome = ANSWERED
    reason = _text(data.get("reason"))
    return {
        "question": _text(data.get("question")) or question,
        "outcome": outcome,
        "unavailable_kind": _kind(outcome, reason),
        "reason": reason if outcome == UNAVAILABLE else None,
        "answer": _text(data.get("answer")),
        "figures": _figures(data),
        "citations": _citations(data),
        "missing_evidence": [
            item for item in (_text(row) for row in _sequence(data.get("missing_evidence"))) if item
        ],
        "as_of": _text(data.get("as_of")),
        "duration_ms": data.get("duration_ms"),
        "asked_at": _text(data.get("started_at")) or _now(),
    }


def _kind(outcome: str, reason: str | None) -> str | None:
    """Which flavour of unavailable this was, for the panel to draw.

    A key is checked before the engine is ever called, so an unavailable that
    reaches here has one: the remaining question is only whether it ran out of
    time or failed some other way.
    """
    if outcome != UNAVAILABLE:
        return None
    lowered = (reason or "").lower()
    if any(hint in lowered for hint in _DEADLINE_HINTS):
        return KIND_TIMEOUT
    return KIND_ENGINE_ERROR


def _figures(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each asserted figure, kept with the query id that produced it.

    A figure the engine did not tie to a query keeps a null `query_id` rather
    than borrowing a neighbour's. The panel then says so instead of drawing a
    citation that does not exist.
    """
    figures = []
    for row in _sequence(data.get("figures")):
        if not isinstance(row, Mapping):
            continue
        figures.append(
            {
                "label": _text(row.get("label")),
                "value": row.get("value"),
                "query_id": _text(row.get("query_id")),
                "tool": _text(row.get("tool")),
            }
        )
    return figures


def _citations(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every tool call the engine made, in the order it made them."""
    citations = []
    for index, row in enumerate(_sequence(data.get("citations"))):
        if not isinstance(row, Mapping):
            continue
        sequence = row.get("sequence")
        citations.append(
            {
                "sequence": index + 1 if sequence is None else sequence,
                "query_id": _text(row.get("query_id")),
                "tool": _text(row.get("tool")),
                "parameters": row.get("parameters"),
                "outcome": _text(row.get("outcome")),
                "executed": row.get("executed", True),
                "duration_ms": row.get("duration_ms"),
            }
        )
    return citations


def _unavailable(
    question: str,
    kind: str,
    detail: str,
    *,
    answer: str | None = _SAME_AS_REASON,
) -> dict[str, Any]:
    """`answer` is the card's body. Pass None where the panel's own wording for
    this state already carries it, so the card does not say it twice."""
    return {
        "question": question,
        "outcome": UNAVAILABLE,
        "unavailable_kind": kind,
        "reason": detail,
        "answer": detail if answer is _SAME_AS_REASON else answer,
        "figures": [],
        "citations": [],
        "missing_evidence": [],
        "as_of": None,
        "duration_ms": None,
        "asked_at": _now(),
    }


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _describe(error: BaseException) -> str:
    detail = str(error).strip()
    return detail and f"The ask engine failed: {detail}" or f"The ask engine failed ({type(error).__name__})."


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
