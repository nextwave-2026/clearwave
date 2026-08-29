#!/usr/bin/env python3
"""Small standard-library runner shared by the evidence-query tools.

Two runners live here and they share everything that is on the wire.

* ``run`` answers from a published fixture. ``external_status`` still uses it:
  it corroborates from a third-party source rather than measuring our events,
  and W3 owns its implementation (DECISIONS.md, 2026-08-29T20:22Z).
* ``run_measured`` answers from the SQLite store through
  ``detector.evidence``. The ten measured tools use it.

Both compute ``query_id`` with the same function over the same canonical
``{tool, input}`` form, and both print one JSON object the same way. A caller
cannot tell from the wire which runner answered, which is the whole point:
every ``query_id`` W3 has already cited keeps resolving to the same call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _error(code: str, message: str) -> int:
    print(json.dumps({"error": {"code": code, "message": message}}, separators=(",", ":")))
    return 1


def run(tool_name: str, fixture_name: str) -> int:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _error("invalid_json", f"stdin must contain one JSON object: {exc.msg}")

    if not isinstance(request, dict):
        return _error("invalid_input", "stdin must contain a JSON object")

    try:
        fixture = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _error("fixture_unavailable", str(exc))

    if not isinstance(fixture, dict) or not isinstance(fixture.get("response"), dict):
        return _error("invalid_fixture", "fixture must contain an object response")

    result: dict[str, Any] = {
        "query_id": query_id(tool_name, request),
        "as_of": fixture.get("as_of"),
        **fixture["response"],
    }
    if not isinstance(result["as_of"], str) or not result["as_of"]:
        return _error("invalid_fixture", "fixture must contain a non-empty as_of timestamp")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def query_id(tool_name: str, request: Any) -> str:
    """The published identifier for the canonical ``{tool, input}`` value.

    Every cited fact in an investigation result resolves through this string,
    so the algorithm is frozen: sha256 over the compact, key-sorted JSON form,
    first sixteen hex characters, prefixed with the tool name.
    """
    material = json.dumps(
        {"tool": tool_name, "input": request},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"q_{tool_name}_{hashlib.sha256(material).hexdigest()[:16]}"


def run_measured(tool_name: str) -> int:
    """Answer one tool call from the measured store, on the same wire shape."""
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _error("invalid_json", f"stdin must contain one JSON object: {exc.msg}")

    if not isinstance(request, dict):
        return _error("invalid_input", "stdin must contain a JSON object")

    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    try:
        from detector import evidence, store
    except ImportError as exc:  # pragma: no cover - a broken checkout, not a call
        return _error("detector_unavailable", f"the W2 detection plane is not importable: {exc}")

    connection = None
    try:
        connection = store.connect(store.database_path())
        response = evidence.answer(tool_name, request, connection)
    except evidence.EvidenceError as exc:
        return _error(exc.code, exc.message)
    except (OSError, sqlite3.Error) as exc:
        return _error("store_unavailable", f"the measurement store could not be read: {exc}")
    finally:
        if connection is not None:
            connection.close()

    as_of = response.get("as_of")
    if not isinstance(as_of, str) or not as_of:
        return _error("invalid_measurement", "a measured response must carry a non-empty as_of")

    print(json.dumps({"query_id": query_id(tool_name, request), **response},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_error("not_a_tool", "invoke a named evidence tool script"))
