"""Adapter for W1's hidden-incident injection.

W4 owns the judge-facing control. W1 owns injection. This module must never
reimplement injection and must never pass a scenario identifier toward
detection or investigation.

It calls W1's real entry point: `worker.inject` builds the command and
publishes it to `worker.helpers.control.CONTROL_TOPIC`, and every running
worker polls that topic each tick and starts or stops its incident live,
with no restart. The command shape is imported rather than hand-rolled so
the two sides cannot drift.

What the control fires is `INJECTED_INCIDENT` below - deliberately one named
constant rather than a scenario catalogue in the UI, and deliberately not a
scenario identifier: it is a cohort scope plus an effect, exactly what W1's
control topic carries. The dashboard is told which cohort was targeted
because the judge is watching a provider degrade; detection is told nothing,
because it reads the traffic, not this module.

The judge control has two live stages over that one target, then a clear:
`developing` publishes a subtle drop, `collapse` the near-total break, and
`clear` stops it. Those three words are the vocabulary of the API, this
adapter, and the UI.

When the broker is unreachable the control reports that instead of claiming a
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

# Measured on the live isolated verify-demo stack (2026-08-30):
#   0.10 too weak to lock merchant-b/adyen on the live mix.
#   0.12 offline on prepare_history volume forms a joint watch; on the live
#     isolated stack after a quiet window it produced no row in 240s.
#   0.15 is the developing magnitude that still sits under the widened
#     watch-hold band (z > -(Z_MIN+2.25)) so stage one can sample watching,
#     while collapse at 0.95 clears that band easily.
# Detection floors were not moved; this is the inject magnitude.
STAGE_DEVELOPING = 0.15
# Today's near-total break, reachable directly as well as after developing.
STAGE_COLLAPSE = 0.95

STAGES = ("developing", "collapse", "clear")

Publisher = Callable[[dict[str, Any]], None]


def resolve_stage(active: bool | None = None, stage: str | None = None) -> str:
    """Map a request onto one of developing, collapse, or clear.

    An explicit stage wins. A bare on/off boolean, including a missing body,
    still means the full break, which is what every existing caller sends.
    """
    if isinstance(stage, str) and stage in STAGES:
        return stage
    if active is None:
        return "clear"
    return "collapse" if active else "clear"


def probability_for(stage: str) -> float | None:
    if stage == "developing":
        return STAGE_DEVELOPING
    if stage == "collapse":
        return STAGE_COLLAPSE
    return None


def injected_incident_command(
    active: bool = True,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    """The exact command the control publishes for a stage."""
    resolved = resolve_stage(active=active, stage=stage)
    if resolved == "clear":
        return stop_command(INJECTED_INCIDENT["merchant_id"])
    return start_command(
        INJECTED_INCIDENT["merchant_id"],
        provider=INJECTED_INCIDENT["provider"],
        effect=INJECTED_INCIDENT["effect"],
        decline_reason=INJECTED_INCIDENT["decline_reason"],
        decline_probability=probability_for(resolved),
    )


def describe(
    active: bool | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    """What the control is, without firing anything. Safe with no broker."""
    resolved = resolve_stage(active=active, stage=stage)
    return {
        "wired": True,
        "active": resolved != "clear",
        "stage": resolved,
        "topic": CONTROL_TOPIC,
        "target": dict(INJECTED_INCIDENT),
        "message": idle_message() if resolved == "clear" else acknowledgement(resolved),
    }


def idle_message() -> str:
    return (
        "Ready. These controls change merchant-b's traffic. "
        "The board will keep showing whatever the store already holds."
    )


def acknowledgement(stage: str) -> str:
    """Immediate account of the judge's action, never of the system's perception."""
    merchant = INJECTED_INCIDENT["merchant_id"]
    provider = INJECTED_INCIDENT["provider"]
    if stage == "developing":
        return (
            f"You started a developing deviation in {merchant}'s traffic "
            f"on provider {provider}. The board is reading the same store "
            f"as any other minute of the day."
        )
    if stage == "collapse":
        return (
            f"You started a collapse in {merchant}'s traffic "
            f"on provider {provider}. The board is reading the same store "
            f"as any other minute of the day."
        )
    return (
        f"You cleared the introduced deviation on {merchant}. "
        f"Traffic returns to its baseline shape."
    )


def fire_hidden_incident(
    active: bool = True,
    publisher: Publisher | None = None,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    """Publish one stage, or report why it did not move.

    `active=True` is the legacy full break (`collapse`); `active=False` is
    `clear`. `stage` is the two-stage vocabulary and wins when it is one of
    developing, collapse, or clear. `publisher` is the seam the offline tests
    drive: the default publishes to a real broker, so no test needs one.
    """
    resolved = resolve_stage(active=active, stage=stage)
    send = publisher or _publish
    command = injected_incident_command(stage=resolved)
    try:
        send(command)
    except Exception as exc:  # noqa: BLE001 - librdkafka raises many types
        # Never report a scenario that did not fire. The judge is entitled to
        # know the difference between "a deviation is running" and "we could
        # not reach the broker".
        return {
            **describe(stage="clear"),
            "requested": resolved,
            "delivered": False,
            "fired": False,
            "error": f"{type(exc).__name__}: {exc}",
            "message": (
                f"Could not reach Kafka on {CONTROL_TOPIC}. Nothing was injected "
                f"and the running workers are unchanged."
            ),
        }

    live = resolved != "clear"
    return {
        **describe(stage=resolved),
        "requested": resolved,
        "delivered": True,
        "fired": live,
        "command": command,
        "message": acknowledgement(resolved),
    }
