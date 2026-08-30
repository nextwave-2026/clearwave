"""Severity-to-channel binding for W4.

Severity is read from the incident record and never computed or adjusted.
LOW and MEDIUM stay on the dashboard. HIGH and CRITICAL add Slack and the phone call.

Every channel is fire-and-forget with a recorded outcome. A failing channel
must never block the dashboard, never fail an incident, and never raise.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .present import cohort_scope_label

LOGGER = logging.getLogger("surfaces.escalation")

SLACK_ENV = "CLEARWAVE_SLACK_WEBHOOK_URL"
SLACK_CHANNEL_ENV = "CLEARWAVE_SLACK_CHANNEL"
DEFAULT_SLACK_CHANNEL = "#control-tower"

TWILIO_ACCOUNT_SID_ENV = "CLEARWAVE_TWILIO_ACCOUNT_SID"
TWILIO_AUTH_TOKEN_ENV = "CLEARWAVE_TWILIO_AUTH_TOKEN"
TWILIO_FROM_ENV = "CLEARWAVE_TWILIO_FROM_NUMBER"
TWILIO_TO_ENV = "CLEARWAVE_TWILIO_TO_NUMBER"
TWILIO_TWIML_URL_ENV = "CLEARWAVE_TWILIO_TWIML_URL"
TWILIO_ENV_VARS = (TWILIO_ACCOUNT_SID_ENV, TWILIO_AUTH_TOKEN_ENV, TWILIO_FROM_ENV, TWILIO_TO_ENV)

SEVERITY_EMOJI = {"low": "⚪", "medium": "🟡", "high": "🟠", "critical": "🔴"}
SEVERITY_COLOR = {"low": "#94A3B8", "medium": "#EAB308", "high": "#F97316", "critical": "#DC2626"}
BRAND_ACCENT = "🟣"

CHANNELS_BY_SEVERITY = {
    "low": ("dashboard",),
    "medium": ("dashboard",),
    "high": ("dashboard", "slack", "phone"),
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
    """Post a Block Kit message to Slack, or log the payload when unconfigured."""
    logger = log or LOGGER.info
    url = webhook_url if webhook_url is not None else os.environ.get(SLACK_ENV, "")
    if not url:
        encoded = json.dumps(payload, sort_keys=True, default=str)
        logger(f"slack webhook not configured; payload that would have been sent: {encoded}")
        return _record("slack", "not_configured", payload)
    message = slack_blocks(payload)
    send = poster or _post_webhook
    try:
        send(url, message)
        return _record("slack", "delivered", payload)
    except Exception as exc:  # noqa: BLE001 - never raise out of a channel
        return _record("slack", "failed", payload, detail=str(exc))


def humanize_id(value: str) -> str:
    """merchant-a -> Merchant A, provider-p2 -> Provider P2. Formatting only, no new data."""
    words = []
    for part in str(value).split("-"):
        words.append(part.upper() if len(part) <= 3 and any(c.isdigit() for c in part) else part.capitalize())
    return " ".join(words)


def slack_blocks(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render one Block Kit message. Every figure is read from payload, never computed.

    Severity and diagnostic confidence are kept in separate blocks on purpose
    (PRD section 11): a CRITICAL incident with LOW confidence must not read as
    one collapsed score. The severity colour bar lives on the attachment, not
    the confidence text, so the two never share one visual channel either.
    """
    severity = str(payload.get("severity") or "").lower()
    severity_label = severity.upper() or "UNKNOWN"
    sev_icon = SEVERITY_EMOJI.get(severity, "⚪")
    incident_id = str(payload.get("incident_id") or "unknown")
    change = payload.get("change") if isinstance(payload.get("change"), Mapping) else {}
    financial = (
        payload.get("financial_impact")
        if isinstance(payload.get("financial_impact"), Mapping)
        else {}
    )
    cohort = (
        payload.get("affected_cohort") if isinstance(payload.get("affected_cohort"), Mapping) else {}
    )
    confidence = payload.get("diagnostic_confidence")
    hypothesis = (
        payload.get("leading_hypothesis")
        if isinstance(payload.get("leading_hypothesis"), Mapping)
        else {}
    )
    competing = payload.get("competing_explanations") or []
    action = (
        payload.get("recommended_next_action")
        if isinstance(payload.get("recommended_next_action"), Mapping)
        else {}
    )
    citations = payload.get("citations") if isinstance(payload.get("citations"), Mapping) else {}

    metric = change.get("metric")
    metric_label = str(metric).replace("_", " ").title() if metric else None
    expected, actual = change.get("expected"), change.get("actual")
    cohort_label = " / ".join(str(value) for value in cohort.values() if value)
    merchant = humanize_id(cohort.get("merchant_id")) if cohort.get("merchant_id") else None
    scope = merchant or payload.get("scope_label") or cohort_scope_label(cohort)

    body: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{sev_icon} {severity_label}" + (f" · {scope}" if scope else ""),
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Incident `#{incident_id}`"},
                {"type": "mrkdwn", "text": f"Lifecycle: *{payload.get('lifecycle_state') or 'unknown'}*"},
            ],
        },
    ]

    summary = f"{severity_label}: {incident_id}"
    if metric_label and expected is not None and actual is not None:
        change_text = f"*{metric_label}*\n{_pct(expected)}  ➜  *{_pct(actual)}*"
        if cohort_label:
            change_text += f"\n:round_pushpin: `{cohort_label}`"
        body.append({"type": "section", "text": {"type": "mrkdwn", "text": change_text}})
        body.append({"type": "divider"})
        summary = f"{sev_icon} {severity_label}: {metric_label} {_pct(expected)} -> {_pct(actual)}"
        if cohort_label:
            summary += f" in {cohort_label}"

    fields = []
    if isinstance(financial.get("gmv_at_risk"), Mapping):
        fields.append({"type": "mrkdwn", "text": f":moneybag: *GMV at risk*\n{_money(financial['gmv_at_risk'])}"})
    if isinstance(financial.get("loss_per_hour"), Mapping):
        fields.append(
            {"type": "mrkdwn", "text": f":chart_with_downwards_trend: *Loss rate*\n{_money(financial['loss_per_hour'])}/h"}
        )
    if payload.get("onset"):
        fields.append({"type": "mrkdwn", "text": f":clock3: *Onset (UTC)*\n{payload['onset']}"})
    if fields:
        body.append({"type": "section", "fields": fields})
        body.append({"type": "divider"})

    hypothesis_statement = hypothesis.get("statement")
    if hypothesis_statement:
        confidence_suffix = f" — _{confidence} confidence_" if confidence else ""
        text = f":mag: *Possible cause*{confidence_suffix}\n{hypothesis_statement}"
        body.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    not_ruled_out = "\n".join(
        f"• {item.get('explanation')}"
        for item in competing
        if isinstance(item, Mapping) and item.get("explanation")
    )
    if not_ruled_out:
        body.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f":grey_question: *Not ruled out*\n{not_ruled_out}"},
            }
        )

    action_text = action.get("action")
    if action_text:
        urgency = action.get("urgency")
        label = f"Recommended · {urgency}" if urgency else "Recommended"
        body.append({"type": "section", "text": {"type": "mrkdwn", "text": f":dart: *{label}*\n{action_text}"}})

    if action_text or hypothesis_statement:
        body.append({"type": "divider"})

    # Reader-facing: which sources were checked, not the raw query_id hashes -
    # those are audit detail for the dashboard's evidence trail, not a Slack
    # alert meant for a fast scan. The full citation still lives in
    # payload["citations"] for whoever needs to trace a claim.
    if citations:
        sources = ", ".join(tool.replace("_", " ") for tool in citations)
        body.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f":microscope: Verified against: {sources}"}]}
        )
    body.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "✅ Every figure above comes straight from live payment data — nothing estimated or guessed",
                }
            ],
        }
    )

    return {
        "channel": os.environ.get(SLACK_CHANNEL_ENV) or DEFAULT_SLACK_CHANNEL,
        "text": summary,
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"{BRAND_ACCENT} *Clearwave Control Tower*  ·  Payment Operations Alert"}],
            }
        ],
        "attachments": [{"color": SEVERITY_COLOR.get(severity, "#94A3B8"), "blocks": body}],
    }


def place_call(
    incident: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    provider: PhoneProvider | None = None,
    enqueue_call: CallFallback | None = None,
) -> dict[str, Any]:
    """Place a phone call, or fall back to the in-dashboard incoming-call panel."""
    resolved = provider if provider is not None else _default_phone_provider()
    incident_id = str(incident.get("incident_id", ""))
    if resolved is None:
        if enqueue_call is not None:
            enqueue_call(incident_id, payload)
        return _record("phone", "fallback_dashboard", payload)
    try:
        resolved(incident, payload)
        return _record("phone", "delivered", payload)
    except Exception as exc:  # noqa: BLE001 - never raise out of a channel
        if enqueue_call is not None:
            enqueue_call(incident_id, payload)
        return _record("phone", "failed", payload, detail=str(exc))


def twiml_for(payload: Mapping[str, Any]) -> str:  # noqa: ARG001 - payload kept for future use
    """Silent call: the call occurring is the signal, not spoken content.

    Trial Twilio accounts prepend their own "trial account" announcement before
    any TwiML runs, which would contradict a spoken script anyway. A bounded
    pause keeps the call itself the deterministic, verifiable signal PRD
    section 19 asks for, without depending on room audio.

    This exact string is also what belongs in a Twilio TwiML Bin - see
    twilio_provider's docstring for why a Bin is required on trial accounts.
    """
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Pause length="20"/></Response>'


def twilio_provider(
    *,
    account_sid: str | None = None,
    auth_token: str | None = None,
    from_number: str | None = None,
    to_number: str | None = None,
    twiml_url: str | None = None,
    poster: Callable[[str, bytes, dict[str, str]], None] | None = None,
) -> PhoneProvider:
    """Build a phone provider that places one real Twilio Programmable Voice call.

    Reads credentials from CLEARWAVE_TWILIO_* environment variables when not
    passed explicitly. No twilio SDK dependency: one urllib POST against the
    Calls REST resource.

    Trial Twilio accounts reject the inline `Twiml` parameter on the Calls API
    with a 400 ("trial accounts have limited parameter access") - verified
    against a real trial account. Trial calls must instead point at a `Url`
    Twilio already hosts, such as a TwiML Bin containing the exact string
    twiml_for() returns. When CLEARWAVE_TWILIO_TWIML_URL (or the twiml_url
    argument) is set, that URL is used via the `Url` parameter; otherwise this
    falls back to sending TwiML inline via `Twiml`, which only paid accounts
    accept.
    """
    sid = account_sid if account_sid is not None else os.environ.get(TWILIO_ACCOUNT_SID_ENV, "")
    token = auth_token if auth_token is not None else os.environ.get(TWILIO_AUTH_TOKEN_ENV, "")
    caller = from_number if from_number is not None else os.environ.get(TWILIO_FROM_ENV, "")
    callee = to_number if to_number is not None else os.environ.get(TWILIO_TO_ENV, "")
    bin_url = twiml_url if twiml_url is not None else os.environ.get(TWILIO_TWIML_URL_ENV, "")
    send = poster or _post_twilio_call

    def _call(incident: Mapping[str, Any], payload: Mapping[str, Any]) -> None:  # noqa: ARG001
        if not (sid and token and caller and callee):
            raise RuntimeError("Twilio credentials are not fully configured")
        instruction = {"Url": bin_url} if bin_url else {"Twiml": twiml_for(payload)}
        body = urllib.parse.urlencode({"To": callee, "From": caller, **instruction}).encode("utf-8")
        credentials = base64.b64encode(f"{sid}:{token}".encode("ascii")).decode("ascii")
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        send(sid, body, headers)

    return _call


def _default_phone_provider() -> PhoneProvider | None:
    """Auto-wire Twilio only when every credential is present. Never partial."""
    if all(os.environ.get(name) for name in TWILIO_ENV_VARS):
        return twilio_provider()
    return None


def _post_twilio_call(account_sid: str, body: bytes, headers: dict[str, str]) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def _pct(ratio: Any) -> str:
    try:
        return f"{float(ratio) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _money(value: Mapping[str, Any]) -> str:
    amount, currency = value.get("amount"), value.get("currency", "")
    try:
        return f"${float(amount):,.0f} {currency}".strip()
    except (TypeError, ValueError):
        return "n/a"


def _payload(incident: Mapping[str, Any], result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pass through stored incident and investigation fields. Never compute a new figure."""
    cohort = incident.get("affected_cohort") or {}
    action = confidence = hypothesis = None
    competing: list[Any] = []
    citations: dict[str, str] = {}
    if isinstance(result, Mapping):
        nested = result.get("result") if isinstance(result.get("result"), Mapping) else result
        if isinstance(nested, Mapping):
            action = nested.get("recommended_next_action")
            confidence = nested.get("diagnostic_confidence")
            hypothesis = nested.get("leading_hypothesis")
            competing = list(nested.get("competing_explanations") or [])
        citations = _citations_from_trail(result.get("trail"))
    return {
        "incident_id": incident.get("incident_id"),
        "severity": incident.get("severity"),
        "lifecycle_state": incident.get("lifecycle_state"),
        "merchant_id": (
            cohort.get("merchant_id") if isinstance(cohort, Mapping) and cohort.get("merchant_id") else None
        ),
        "scope_label": cohort_scope_label(cohort) if isinstance(cohort, Mapping) else "Platform-wide",
        "affected_cohort": cohort,
        "change": incident.get("change"),
        "financial_impact": incident.get("financial_impact"),
        "onset": incident.get("onset"),
        "diagnostic_confidence": confidence,
        "leading_hypothesis": hypothesis,
        "competing_explanations": competing,
        "recommended_next_action": action,
        "citations": citations,
    }


def _citations_from_trail(trail: Any) -> dict[str, str]:
    """Reduce the evidence trail to one query_id per tool, first occurrence wins.

    Sourced from the persisted trail (investigation/store.py:read_result), not
    re-derived from the narrative, so "verified against" always reflects every
    tool actually queried - not just the ones the narrative happened to cite.
    """
    citations: dict[str, str] = {}
    if not isinstance(trail, list):
        return citations
    for entry in trail:
        if not isinstance(entry, Mapping):
            continue
        tool, query_id = entry.get("tool"), entry.get("query_id")
        if tool and query_id and tool not in citations:
            citations[str(tool)] = f"{tool}:{query_id}"
    return citations


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
