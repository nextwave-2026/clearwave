#!/usr/bin/env python3
"""Show the Twilio phone request without placing a call."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from surfaces.escalation import (  # noqa: E402
    TWILIO_ACCOUNT_SID_ENV,
    TWILIO_AUTH_TOKEN_ENV,
    TWILIO_ENV_VARS,
    TWILIO_FROM_ENV,
    TWILIO_TO_ENV,
    TWILIO_TWIML_URL_ENV,
    twilio_provider,
    twiml_for,
)


def _masked_sid(value: str) -> str:
    return f"{value[:6]}...{value[-4:]}" if len(value) > 10 else "(unset)"


def _configuration() -> None:
    sid = os.environ.get(TWILIO_ACCOUNT_SID_ENV, "")
    print("Configuration:")
    print(f"  {TWILIO_ACCOUNT_SID_ENV:34} {_masked_sid(sid)}")
    print(
        f"  {TWILIO_AUTH_TOKEN_ENV:34} "
        f"{'set (hidden)' if os.environ.get(TWILIO_AUTH_TOKEN_ENV) else '(unset)'}"
    )
    for name in (TWILIO_FROM_ENV, TWILIO_TO_ENV, TWILIO_TWIML_URL_ENV):
        print(f"  {name:34} {os.environ.get(name) or '(unset)'}")


def _warn_if_bin_is_unset() -> bool:
    if os.environ.get(TWILIO_TWIML_URL_ENV):
        return True
    print()
    print(f"WARNING: {TWILIO_TWIML_URL_ENV} is not set.")
    print("  The request will use inline TwiML, which a trial account rejects with HTTP 400.")
    print("  Create a TwiML Bin and set this variable before a live demo.")
    print(f"  TwiML Bin body: {twiml_for({})}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="accepted for compatibility; this command is always a dry run",
    )
    parser.parse_args()

    _configuration()
    missing = [name for name in TWILIO_ENV_VARS if not os.environ.get(name)]
    if missing:
        print()
        print("Required variables missing:")
        for name in missing:
            print(f"  - {name}")
        return 1

    has_bin = _warn_if_bin_is_unset()
    captured: dict[str, object] = {}

    def capture(account_sid: str, body: bytes, headers: dict[str, str]) -> None:
        captured["account_sid"] = account_sid
        captured["body"] = body.decode("utf-8")
        captured["headers"] = headers

    twilio_provider(poster=capture)({}, {})
    body = str(captured["body"])
    print()
    print("Request that would be sent:")
    print(
        "  POST https://api.twilio.com/2010-04-01/Accounts/"
        f"{_masked_sid(str(captured['account_sid']))}/Calls.json"
    )
    print("  Authorization: Basic [redacted]")
    for key, value in urllib.parse.parse_qsl(body):
        print(f"  {key} = {value}")
    print(f"  instruction parameter: {'Url (TwiML Bin)' if has_bin else 'Twiml (inline)'}")
    print()
    print("Dry run: no call placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
