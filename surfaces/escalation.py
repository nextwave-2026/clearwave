"""Severity-to-channel binding for W4.

Severity is read from the incident record and never computed or adjusted.
LOW and MEDIUM stay on the dashboard. HIGH adds a Slack-style notification.
CRITICAL adds the phone call.

Every channel is fire-and-forget with a recorded outcome. A failing channel
must never block the dashboard, never fail an incident, and never raise.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

LOGGER = logging.getLogger("surfaces.escalation")

SLACK_ENV = "CLEARWAVE_SLACK_WEBHOOK_URL"
PHONE_ENV = "CLEARWAVE_PHONE_PROVIDER"

CHANNELS_BY_SEVERITY = {
    "low": ("dashboard",),
    "medium": ("dashboard",),
    "high": ("dashboard", "slack"),
    "critical": ("dashboard", "slack", "phone"),
}

Poster = Callable[[str, Mapping[str, Any]], None]
PhoneProvider = Callable[[Mapping[str, Any], Mapping[str, Any]], None]
Logger = Callable[[str], None]
CallFallback = Callable[[str, Mapping[str, Any]], None]


def channels_for(severity: str) -> tuple[str, ...]:
    """Return the stored-severity binding. Unknown labels stay dashboard-only."""
    return CHANNELS_BY_SEVERITY.get(str(severity).lower(), ("dashboard",))


def escalate(
    incident: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
    *,
    slack_url: str | None = None,
    poster: Poster | None = None,
    phone_provider: PhoneProvider | None = None,
    log: Logger | None = None,
    enqueue_call: CallFallback | None = None,
) -> list[dict[str, Any]]:
    """Dispatch every bound channel. Never raises."""
    logger = log or LOGGER.info
    payload = _payload(incident, result)
    outcomes: list[dict[str, Any]] = []
    severity = str(incident.get("severity", ""))
    for channel in channels_for(severity):
        try:
            if channel == "dashboard":
                outcomes.append(_record(channel, "delivered", payload))
            elif channel == "slack":
                outcomes.append(
                    notify_slack(payload, webhook_url=slack_url, poster=poster, log=logger)
                )
            elif channel == "phone":
                outcomes.append(
                    place_call(
                        incident,
                        payload,
                        provider=phone_provider,
                        enqueue_call=enqueue_call,
                    )
                )
            else:
                outcomes.append(_record(channel, "failed", payload, detail="unknown channel"))
        except Exception as exc:  # noqa: BLE001 - fire-and-forget must swallow
            outcomes.append(_record(channel, "failed", payload, detail=str(exc)))
    return outcomes


def notify_slack(
    payload: Mapping[str, Any],
    *,
    webhook_url: str | None = None,
    poster: Poster | None = None,
    log: Logger | None = None,
) -> dict[str, Any]:
    """Post to Slack, or log the exact payload when no webhook is configured."""
    logger = log or LOGGER.info
    url = webhook_url if webhook_url is not None else os.environ.get(SLACK_ENV, "")
    if not url:
        encoded = json.dumps(payload, sort_keys=True, default=str)
        logger(f"slack webhook not configured; payload that would have been sent: {encoded}")
        return _record("slack", "not_configured", payload)
    send = poster or _post_webhook
    try:
        send(url, payload)
        return _record("slack", "delivered", payload)
    except Exception as exc:  # noqa: BLE001 - never raise out of a channel
        return _record("slack", "failed", payload, detail=str(exc))


def place_call(
    incident: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    provider: PhoneProvider | None = None,
    enqueue_call: CallFallback | None = None,
) -> dict[str, Any]:
    """Place a phone call, or fall back to the in-dashboard incoming-call panel."""
    configured = provider if provider is not None else os.environ.get(PHONE_ENV, "")
    incident_id = str(incident.get("incident_id", ""))
    if not configured:
        if enqueue_call is not None:
            enqueue_call(incident_id, payload)
        return _record("phone", "fallback_dashboard", payload)
    try:
        if callable(configured):
            configured(incident, payload)
        else:
            return _record(
                "phone",
                "failed",
                payload,
                detail="no telephony SDK is bundled; use the dashboard fallback",
            )
        return _record("phone", "delivered", payload)
    except Exception as exc:  # noqa: BLE001 - never raise out of a channel
        if enqueue_call is not None:
            enqueue_call(incident_id, payload)
        return _record("phone", "failed", payload, detail=str(exc))


def _payload(incident: Mapping[str, Any], result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pass through stored incident fields. Do not compute new figures."""
    cohort = incident.get("affected_cohort") or {}
    action = None
    if isinstance(result, Mapping):
        nested = result.get("result") if isinstance(result.get("result"), Mapping) else result
        if isinstance(nested, Mapping):
            action = nested.get("recommended_next_action")
    return {
        "incident_id": incident.get("incident_id"),
        "severity": incident.get("severity"),
        "lifecycle_state": incident.get("lifecycle_state"),
        "merchant_id": cohort.get("merchant_id") if isinstance(cohort, Mapping) else None,
        "affected_cohort": cohort,
        "change": incident.get("change"),
        "financial_impact": incident.get("financial_impact"),
        "onset": incident.get("onset"),
        "recommended_next_action": action,
    }


def _record(
    channel: str,
    status: str,
    payload: Mapping[str, Any],
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    record = {"channel": channel, "status": status, "payload": dict(payload)}
    if detail is not None:
        record["detail"] = detail
    return record


def _post_webhook(url: str, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()
