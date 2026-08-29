#!/usr/bin/env python3
"""Smoke-test the W4 phone channel against the real Twilio API.

Why this exists: `surfaces.escalation.place_call` deliberately swallows every
exception, because PRD section 29 requires a failing channel never to block the
dashboard or fail an incident. That is right in production and useless when you
are trying to find out why the phone did not ring. This script calls
`twilio_provider` directly so the real error surfaces, and reads Twilio's
response body, which is where the useful message lives.

It also guards the trap that costs the most time: on a trial account the Calls
API rejects inline TwiML with HTTP 400, so CLEARWAVE_TWILIO_TWIML_URL must point
at a TwiML Bin. The four credential variables alone look complete enough to
auto-wire, so the failure only shows up as a call that never arrives.

Usage:
    python3 scripts/check_phone_channel.py --dry-run   # show the request, call nobody
    python3 scripts/check_phone_channel.py             # place a real call
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
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

# Stands in for a critical C3 record. The provider ignores both arguments; they
# exist so this exercises the same call signature `place_call` uses.
SAMPLE_INCIDENT = {"incident_id": "phone-channel-check", "severity": "critical"}
SAMPLE_PAYLOAD = {"incident_id": "phone-channel-check", "severity": "critical"}


def describe_environment() -> list[str]:
    """Report which required variables are missing, by name."""
    return [name for name in TWILIO_ENV_VARS if not os.environ.get(name)]


def summarise_configuration() -> None:
    """Print what is configured. Never prints the auth token."""
    sid = os.environ.get(TWILIO_ACCOUNT_SID_ENV, "")
    print("Configuration:")
    print(f"  {TWILIO_ACCOUNT_SID_ENV:34} {sid[:6] + '...' + sid[-4:] if len(sid) > 10 else '(unset)'}")
    print(f"  {TWILIO_AUTH_TOKEN_ENV:34} {'set (hidden)' if os.environ.get(TWILIO_AUTH_TOKEN_ENV) else '(unset)'}")
    print(f"  {TWILIO_FROM_ENV:34} {os.environ.get(TWILIO_FROM_ENV) or '(unset)'}")
    print(f"  {TWILIO_TO_ENV:34} {os.environ.get(TWILIO_TO_ENV) or '(unset)'}")
    print(f"  {TWILIO_TWIML_URL_ENV:34} {os.environ.get(TWILIO_TWIML_URL_ENV) or '(unset)'}")


def warn_about_missing_bin() -> bool:
    """Warn when no TwiML Bin is set. Returns True when the Bin is present."""
    if os.environ.get(TWILIO_TWIML_URL_ENV):
        return True
    print()
    print(f"WARNING: {TWILIO_TWIML_URL_ENV} is not set.")
    print("  The call will send TwiML inline, which a TRIAL account rejects with HTTP 400")
    print('  ("trial accounts have limited parameter access"). Only paid accounts accept it.')
    print("  Create a TwiML Bin at console.twilio.com -> Developer tools -> TwiML Bins")
    print("  containing exactly this, then set the variable to the Bin's URL:")
    print()
    print(f"    {twiml_for({})}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and show the exact request without placing a call",
    )
    args = parser.parse_args()

    summarise_configuration()

    missing = describe_environment()
    if missing:
        print()
        print("Cannot place a call. These required variables are unset:")
        for name in missing:
            print(f"  - {name}")
        print()
        print("Set them in this shell (not in .env - the .env loader only covers OpenAI settings,")
        print("while surfaces/escalation.py reads the process environment directly).")
        return 1

    has_bin = warn_about_missing_bin()

    captured: dict[str, object] = {}

    def capture(account_sid: str, body: bytes, headers: dict[str, str]) -> None:
        captured["account_sid"] = account_sid
        captured["body"] = body.decode("utf-8")
        captured["headers"] = headers

    print()
    print("Request Twilio will receive:")
    twilio_provider(poster=capture)(SAMPLE_INCIDENT, SAMPLE_PAYLOAD)
    body = str(captured["body"])
    print(f"  POST https://api.twilio.com/2010-04-01/Accounts/{captured['account_sid']}/Calls.json")
    for key, value in urllib.parse.parse_qsl(body):
        print(f"  {key} = {value}")
    print(f"  instruction parameter: {'Url (TwiML Bin)' if has_bin else 'Twiml (inline - trial will reject)'}")

    if args.dry_run:
        print()
        print("Dry run: no call placed.")
        return 0

    print()
    print("Placing the call...")
    try:
        twilio_provider()(SAMPLE_INCIDENT, SAMPLE_PAYLOAD)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"FAILED: HTTP {exc.code} {exc.reason}")
        print(f"Twilio said: {detail}")
        return 1
    except Exception as exc:  # noqa: BLE001 - this script exists to surface the error
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    print("Twilio accepted the call. The destination phone should ring shortly.")
    print("Expect the trial-account announcement first, then about 20 seconds of silence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
