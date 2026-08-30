"""Adapter for W1's hidden-incident injection.

W4 owns the judge-facing control. W1 owns injection. This module must never
reimplement injection and must never pass a scenario identifier toward
detection or investigation.

It calls W1's real entry point: `worker.inject` builds the command and
publishes it to `worker.helpers.control.CONTROL_TOPIC`, and every running
worker polls that topic each tick and starts or stops its incident live,
with no restart. The command shape is imported rather than hand-rolled so
the two sides cannot drift.

What the toggle fires is `INJECTED_INCIDENT` below - deliberately one named
constant rather than a scenario catalogue in the UI, and deliberately not a
scenario identifier: it is a cohort scope plus an effect, exactly what W1's
control topic carries. The dashboard is told which cohort was targeted
because the judge is watching a provider degrade; detection is told nothing,
because it reads the traffic, not this module.

When the broker is unreachable the toggle reports that instead of claiming a
scenario fired. That honesty is the one thing the previous dead adapter got
right and it is preserved here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from worker.helpers.control import CONTROL_TOPIC
from worker.inject import publish as _publish
from worker.inject import start_command, stop_command

# The provider-degradation shape the demo leans on: a provider-scoped decline
# on one of the three running merchants. merchant-b runs adyen, and
# `provider_timeout` is the native decline reason W1 emits when a provider
# degrades, so the traffic the judge sees is the traffic the demo is about.
INJECTED_INCIDENT = {
    "merchant_id": "merchant-b",
    "provider": "adyen",
    "effect": "decline",
    "decline_reason": "provider_timeout",
}

Publisher = Callable[[dict[str, Any]], None]


def injected_incident_command(active: bool) -> dict[str, Any]:
    """The exact command the toggle publishes in each direction."""
    if not active:
        return stop_command(INJECTED_INCIDENT["merchant_id"])
    return start_command(
        INJECTED_INCIDENT["merchant_id"],
        provider=INJECTED_INCIDENT["provider"],
        effect=INJECTED_INCIDENT["effect"],
        decline_reason=INJECTED_INCIDENT["decline_reason"],
    )


def describe(active: bool | None = None) -> dict[str, Any]:
    """What the control is, without firing anything. Safe with no broker."""
    return {
        "wired": True,
        "active": bool(active),
        "topic": CONTROL_TOPIC,
        "target": dict(INJECTED_INCIDENT),
    }


def fire_hidden_incident(
    active: bool = True,
    publisher: Publisher | None = None,
) -> dict[str, Any]:
    """Toggle W1's hidden incident on or off, or report why it did not move.

    `active=True` publishes the start command, `active=False` the stop command.
    `publisher` is the seam the offline tests drive: the default publishes to a
    real broker, so no test needs one.
    """
    send = publisher or _publish
    command = injected_incident_command(active)
    try:
        send(command)
    except Exception as exc:  # noqa: BLE001 - librdkafka raises many types
        # Never report a scenario that did not fire. The judge is entitled to
        # know the difference between "an incident is running" and "we could
        # not reach the broker".
        return {
            **describe(active=False),
            "requested": "start" if active else "stop",
            "delivered": False,
            "fired": False,
            "error": f"{type(exc).__name__}: {exc}",
            "message": (
                f"Could not reach Kafka on {CONTROL_TOPIC}. Nothing was injected "
                f"and the running workers are unchanged."
            ),
        }

    if active:
        message = (
            f"Injected a {INJECTED_INCIDENT['effect']} on provider "
            f"{INJECTED_INCIDENT['provider']} for {INJECTED_INCIDENT['merchant_id']}. "
            f"Detection is not told which scenario this is."
        )
    else:
        message = (
            f"Cleared the injected incident on {INJECTED_INCIDENT['merchant_id']}. "
            f"Traffic returns to its baseline shape."
        )
    return {
        **describe(active=active),
        "requested": "start" if active else "stop",
        "delivered": True,
        "fired": bool(active),
        "command": command,
        "message": message,
    }
