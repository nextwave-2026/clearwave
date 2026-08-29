"""List the OpenAI models reachable with the configured credentials.

Run with ``python3 -m investigation.models`` after setting OPENAI_API_KEY.
The command prints model ids only, one per line.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment]

from .env import (
    MISSING_KEY_MESSAGE,
    api_key_present,
    load_dotenv,
    openai_client_kwargs,
    redact_secrets,
)


def discover_models(client: Any | None = None) -> list[str]:
    """Return sorted ids from the configured client's models endpoint."""
    if client is not None:
        configured = client
    else:
        client_type = OpenAI
        if client_type is None:
            from openai import OpenAI as client_type
        configured = client_type(**openai_client_kwargs())
    listing = configured.models.list()
    data = _value(listing, "data", listing)
    if not isinstance(data, Iterable) or isinstance(data, (str, bytes, Mapping)):
        return []
    ids = {
        str(model_id)
        for item in data
        if (model_id := _value(item, "id"))
    }
    return sorted(ids)


def main(argv: list[str] | None = None) -> int:
    """Print reachable model ids, or one safe actionable error line."""
    del argv
    load_dotenv()
    if not api_key_present():
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return 1
    try:
        for model_id in discover_models():
            print(model_id)
    except Exception as exc:
        print(f"investigation.models: {redact_secrets(str(exc))}", file=sys.stderr)
        return 1
    return 0


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["discover_models", "main"]
