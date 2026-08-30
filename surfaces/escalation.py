"""Severity-to-channel binding for W4.

Severity is read from the incident record and never computed or adjusted.
LOW and MEDIUM stay on the dashboard. HIGH adds Slack. Only CRITICAL adds the phone call.

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
from datetime import datetime
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

# Headroom under Slack's real limits (~3000 chars per mrkdwn section text,
# 150 for a header's plain_text) - narrative fields are LLM-authored and
# unbounded (investigation/contracts.py sets no max_length), and Slack
# rejects the WHOLE message on overflow, which would silently fail the
# primary channel for a critical incident.
SECTION_TEXT_LIMIT = 2900
HEADER_TEXT_LIMIT = 140
# A readability bound, not a Slack one: an LLM-written cause or action that
# runs to a paragraph is what turns a five-second alert into a wall of text.
# The full narrative is on the dashboard.
NARRATIVE_LINE_LIMIT = 240
_TRUNCATION_SUFFIX = "…"

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
    channels: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Dispatch every bound channel. Never raises.

    `channels` narrows dispatch to a subset the caller has already decided to
    fire; it never widens it. It exists because escalation is claimed per
    channel per incident (`surfaces/store.py`), so an incident re-measured
    from `high` to `critical` must fire the newly bound phone channel WITHOUT
    posting to Slack a second time. Passing it does not change the binding:
    `CHANNELS_BY_SEVERITY` stays the only severity-to-channel mapping, and the
    caller's subset is intersected with it rather than trusted, so no caller
    can reach a channel this severity does not bind. Default None means "every
    channel bound at this severity", which is what every other caller wants.
    """
    logger = log or LOGGER.info
    payload = _payload(incident, result)
    outcomes: list[dict[str, Any]] = []
    severity = str(incident.get("severity", ""))
    bound = channels_for(severity)
    selected = bound if channels is None else tuple(c for c in bound if c in set(channels))
    for channel in selected:
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


def _truncate(text: str | None, limit: int, *, suffix: str = _TRUNCATION_SUFFIX) -> str | None:
    """Bound text length so Slack never rejects the whole message on overflow."""
    if text is None:
        return None
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(limit - len(suffix), 0)].rstrip() + suffix


def humanize_id(value: str) -> str:
    """merchant-a -> Merchant A, provider-p2 -> Provider P2. Formatting only, no new data."""
    words = []
    for part in str(value).split("-"):
        words.append(part.upper() if len(part) <= 3 and any(c.isdigit() for c in part) else part.capitalize())
    return " ".join(words)


def _money_if_present(value: Any) -> str | None:
    if not isinstance(value, Mapping) or value.get("amount") is None:
        return None
    rendered = _money(value)
    return None if rendered == "n/a" else rendered


def _cohort_line(cohort: Mapping[str, Any], scope: str | None = None) -> str | None:
    """One scannable line naming the slice: Merchant A · Provider P2 · CO · card.

    A labelled field grid is more complete and slower to read; the same values
    stay in payload["affected_cohort"] and on the dashboard for whoever needs
    them spelled out. Whatever the header already names as the scope is left
    out here, so the reader's eye is never spent twice on one value.
    """
    order = (
        "merchant_id",
        "provider",
        "country",
        "payment_method",
        "card_network",
        "issuing_bank",
        "decline_code",
    )
    parts = []
    for key in order:
        value = cohort.get(key)
        if not value:
            continue
        rendered = _cohort_value(key, value)
        if scope and rendered in scope:
            continue
        parts.append(f"decline: {rendered}" if key == "decline_code" else rendered)
    return " · ".join(parts) or None


def _cohort_value(key: str, value: Any) -> str:
    text = str(value)
    if key in {"merchant_id", "provider"}:
        return humanize_id(text)
    return text.replace("_", " ")


def slack_blocks(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render one Block Kit message. Every figure is read from payload, never computed.

    Shaped for a five-second read: severity and scope in the header, the metric
    move and the money on one line under it, cause and next action on a second,
    and everything an operator does not need in those five seconds - incident
    id, lifecycle, evidence sources, the no-remediation note - in one grey
    footer. The previous rendering printed the loss rate, the GMV and the
    metric move two and three times each across an executive readout, a change
    section and a field grid. Nothing here is computed differently; the
    duplicate readouts were removed, not recalculated.

    Severity and diagnostic confidence still render in separate blocks (PRD
    section 11): a CRITICAL incident with LOW confidence must never read as one
    collapsed score. The severity colour bar lives on the attachment, so the
    two never share one visual channel either.
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
    has_change = bool(metric_label) and expected is not None and actual is not None
    merchant = humanize_id(cohort.get("merchant_id")) if cohort.get("merchant_id") else None
    scope = merchant or payload.get("scope_label") or cohort_scope_label(cohort)
    cohort_line = _cohort_line(cohort, str(scope) if scope else None)
    gmv_at_risk = _money_if_present(financial.get("gmv_at_risk"))
    loss_per_hour = _money_if_present(financial.get("loss_per_hour"))

    body: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": _truncate(
                    f"{sev_icon} {severity_label}" + (f" · {scope}" if scope else ""),
                    HEADER_TEXT_LIMIT,
                ),
                "emoji": True,
            },
        }
    ]

    # What is happening and what it costs - the lines this alert exists for.
    headline: list[str] = []
    if has_change:
        headline.append(f"*{metric_label} {_pct(expected)} ➜ {_pct(actual)}*")
    money = []
    if loss_per_hour:
        money.append(f"*{loss_per_hour}/h* lost")
    if gmv_at_risk:
        money.append(f"{gmv_at_risk} at risk")
    if payload.get("onset"):
        money.append(f"since {_clock(payload['onset'])}")
    if money:
        headline.append(" · ".join(money))
    if cohort_line:
        headline.append(cohort_line)
    if headline:
        body.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate("\n".join(headline), SECTION_TEXT_LIMIT),
                },
            }
        )

    # Why, and what to do about it. One block, so the eye lands once.
    hypothesis_statement = hypothesis.get("statement")
    action_text = action.get("action")
    diagnosis: list[str] = []
    if hypothesis_statement:
        label = f"Likely cause ({confidence} confidence)" if confidence else "Likely cause"
        diagnosis.append(f"*{label}:* {_truncate(str(hypothesis_statement), NARRATIVE_LINE_LIMIT)}")
    not_ruled_out = " · ".join(
        str(item.get("explanation"))
        for item in competing
        if isinstance(item, Mapping) and item.get("explanation")
    )
    if not_ruled_out:
        # Kept even in the short form: a leading hypothesis shown alone reads as
        # a settled cause, which is the fabricated certainty ADR 0007 forbids.
        diagnosis.append(f"_Not ruled out:_ {_truncate(not_ruled_out, NARRATIVE_LINE_LIMIT)}")
    if action_text:
        urgency = action.get("urgency")
        # "Do now" already says urgency: now. Only a different urgency adds a word.
        suffix = f" _({urgency})_" if urgency and str(urgency).lower() != "now" else ""
        diagnosis.append(f"*Do now:*{suffix} {_truncate(str(action_text), NARRATIVE_LINE_LIMIT)}")
    if confidence and not hypothesis_statement:
        # Confidence never rides on the severity block; with no hypothesis to
        # attach it to it still gets its own line rather than being dropped.
        diagnosis.append(f"*Diagnostic confidence:* {confidence}")
    if diagnosis:
        body.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate("\n".join(diagnosis), SECTION_TEXT_LIMIT),
                },
            }
        )

    # Everything an operator does not need inside the first five seconds. Tool
    # names, not the raw query_id hashes - the full citation stays in
    # payload["citations"] for the dashboard's evidence trail.
    footer = [f"`#{incident_id}`", str(payload.get("lifecycle_state") or "unknown")]
    if citations:
        footer.append("Verified against " + ", ".join(tool.replace("_", " ") for tool in citations))
    if action_text:
        # ADR 0029: a message carrying a recommendation says on that same
        # message that nothing was executed.
        footer.append("No automatic remediation was executed")
    body.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": _truncate("  ·  ".join(footer), SECTION_TEXT_LIMIT)}
            ],
        }
    )

    summary = f"{sev_icon} {severity_label}: {scope}" if scope else f"{severity_label}: {incident_id}"
    if has_change:
        summary += f" · {metric_label} {_pct(expected)} ➜ {_pct(actual)}"
    if loss_per_hour:
        summary += f" · {loss_per_hour}/h"

    return {
        "channel": os.environ.get(SLACK_CHANNEL_ENV) or DEFAULT_SLACK_CHANNEL,
        "text": summary,
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"{BRAND_ACCENT} *Clearwave Control Tower*"}],
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


def _clock(onset: Any) -> str:
    """2026-08-30T02:14:00Z -> 02:14 UTC. Formatting only; unparseable text passes through.

    A full ISO timestamp costs a second of reading to answer "when did this
    start"; the exact value stays in payload["onset"] and on the dashboard.
    """
    text = str(onset)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return f"{moment:%H:%M} UTC"


def _pct(ratio: Any) -> str:
    try:
        return f"{float(ratio) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _money(value: Mapping[str, Any]) -> str:
    amount, currency = value.get("amount"), value.get("currency", "")
    try:
        numeric = float(amount)
        sign = "-" if numeric < 0 else ""
        return f"{sign}${abs(numeric):,.0f} {currency}".strip()
    except (TypeError, ValueError):
        return "n/a"


def _payload(incident: Mapping[str, Any], result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pass through stored incident and investigation fields. Never compute a new figure."""
    cohort = incident.get("affected_cohort") or {}
    action = confidence = hypothesis = None
    competing: list[Any] = []
    citations: dict[str, str] = {}
    if isinstance(result, Mapping):
        # When the investigation could not run, its narrative fields hold
        # placeholder text like "Causal investigation unavailable: ..."
        # (investigation/degrade.py). The C5 contract requires these null in
        # that case rather than passed through as if they were a real
        # diagnosis - a critical incident must never look like it has a
        # cause when it does not.
        if result.get("outcome") != "agent_unavailable":
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
