"""Load OpenAI settings from the environment, with an optional root .env file.

The investigation agent reads its OpenAI settings from the process environment
only. The CLI may copy values from a gitignored ``.env`` into that environment
first. Existing environment variables always win. The key is never returned in
logs or error text.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
REDACTED = "[redacted]"
MISSING_KEY_MESSAGE = (
    "vertical-path: OPENAI_API_KEY is not set; copy .env.example to .env and set it"
)


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Comments and blank lines are ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = _unquote(value.strip())
    return values


def load_dotenv(
    path: Path | str | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy unset keys from a ``.env`` file into ``environ``.

    Returns the keys that were applied. Keys already present in ``environ``
    are left untouched, so the process environment wins over the file.
    """
    env = os.environ if environ is None else environ
    target = Path(path) if path is not None else ROOT / ".env"
    if not target.is_file():
        return {}
    loaded = parse_env_file(target.read_text(encoding="utf-8"))
    applied: dict[str, str] = {}
    for key, value in loaded.items():
        if key in env:
            continue
        env[key] = value
        applied[key] = value
    return applied


def api_key() -> str | None:
    """Return OPENAI_API_KEY from the environment only, or None if unset."""
    value = os.environ.get("OPENAI_API_KEY")
    return value if value else None


def api_key_present() -> bool:
    return api_key() is not None


def openai_client_kwargs() -> dict[str, str]:
    """Keyword arguments for the OpenAI client, taken from the environment."""
    kwargs: dict[str, str] = {}
    key = api_key()
    if key is not None:
        kwargs["api_key"] = key
    base = os.environ.get("OPENAI_BASE_URL") or ""
    if base:
        kwargs["base_url"] = base
    return kwargs


def openai_model(default: str) -> str:
    return os.environ.get("OPENAI_MODEL") or default


def openai_reasoning_effort(default: str | None = None) -> str | None:
    """Return configured reasoning effort, or a supplied model default."""
    value = os.environ.get("OPENAI_REASONING_EFFORT", "").strip()
    return value or default


def openai_max_output_tokens(default: int) -> int:
    """Return a positive configurable output ceiling, or the supplied default."""
    value = os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "").strip()
    if not value:
        return default
    try:
        ceiling = int(value)
    except ValueError as exc:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be a positive integer") from exc
    if ceiling <= 0:
        raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be a positive integer")
    return ceiling


def redact_secrets(text: str) -> str:
    """Replace any live API key with a fixed placeholder, never a key fragment."""
    key = os.environ.get("OPENAI_API_KEY") or ""
    if key:
        text = text.replace(key, REDACTED)
    return text


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def missing_key_message() -> str:
    return MISSING_KEY_MESSAGE


__all__ = [
    "ENV_EXAMPLE",
    "MISSING_KEY_MESSAGE",
    "REDACTED",
    "ROOT",
    "api_key",
    "api_key_present",
    "load_dotenv",
    "missing_key_message",
    "openai_client_kwargs",
    "openai_model",
    "openai_reasoning_effort",
    "openai_max_output_tokens",
    "parse_env_file",
    "redact_secrets",
]
