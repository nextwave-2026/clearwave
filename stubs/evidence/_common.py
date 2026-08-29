#!/usr/bin/env python3
"""Small standard-library runner shared by the evidence-query stubs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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

    query_material = json.dumps(
        {"tool": tool_name, "input": request},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    query_id = f"q_{tool_name}_{hashlib.sha256(query_material).hexdigest()[:16]}"

    result: dict[str, Any] = {
        "query_id": query_id,
        "as_of": fixture.get("as_of"),
        **fixture["response"],
    }
    if not isinstance(result["as_of"], str) or not result["as_of"]:
        return _error("invalid_fixture", "fixture must contain a non-empty as_of timestamp")

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_error("not_a_tool", "invoke a named evidence tool script"))
