"""Shape stored C3/C4 records for the dashboard. No domain arithmetic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INACTIVE_STATES = {"resolved", "mitigated"}

# A watch is not an incident. It is a developing deviation the detector
# deliberately chose not to report, carried on the same C3 row the cohort will
# keep if it becomes one (docs/contracts/incident.md). Counting it as active
# would put a warning we chose not to page on into the "Right now" business
# figures and into the incident queue styled like a crossed floor, which is the
# one thing this rail exists to avoid.
WATCHING_STATE = "watching"

_SCOPE_ORDER = (
    "provider",
    "payment_method",
    "card_network",
    "country",
    "issuing_bank",
)
_SCOPE_NOUN = {
    "provider": "Provider",
    "payment_method": "Payment method",
    "card_network": "Card network",
    "country": "Country",
    "issuing_bank": "Issuing bank",
}


def overview(
    incidents: list[Mapping[str, Any]],
    investigations: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Business overview figures, copied from the leading live incident."""
    results = investigations or {}
    active = [incident for incident in incidents if _is_active(incident)]
    headline = active[0] if active else None
    change = _mapping(headline.get("change") if headline else None)
    financial = _mapping(headline.get("financial_impact") if headline else None)
    return {
        "active_incident_count": len(active),
        "source_incident_id": None if headline is None else headline.get("incident_id"),
        "current_conversion": change.get("actual"),
        "expected_conversion": change.get("expected"),
        "gmv": financial.get("attempted_value"),
        "estimated_gmv_at_risk": financial.get("gmv_at_risk"),
        "change": change or None,
        "financial_impact": financial or None,
        "merchant_health": merchant_health(incidents),
        "incidents": [queue_item(incident, results.get(str(incident.get("incident_id")))) for incident in active],
        "watches": watch_items(incidents),
    }


def queue(
    incidents: list[Mapping[str, Any]],
    investigations: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    results = investigations or {}
    return {
        "incidents": [
            queue_item(incident, results.get(str(incident.get("incident_id"))))
            for incident in incidents
            if not _is_watch(incident)
        ],
        "watches": watch_items(incidents),
    }


def queue_item(
    incident: Mapping[str, Any],
    investigation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Severity and diagnostic confidence travel as independent fields."""
    narrative_available = _narrative_available(investigation)
    result_body = _result_body(investigation) if narrative_available else None
    confidence = result_body.get("diagnostic_confidence") if result_body else None
    return {
        "incident_id": incident.get("incident_id"),
        "severity": incident.get("severity"),
        "diagnostic_confidence": confidence,
        "lifecycle_state": incident.get("lifecycle_state"),
        "outcome": None if investigation is None else investigation.get("outcome"),
        "onset": incident.get("onset"),
        "affected_cohort": incident.get("affected_cohort"),
        "scope_label": cohort_scope_label(_mapping(incident.get("affected_cohort"))),
        "change": incident.get("change"),
        "financial_impact": incident.get("financial_impact"),
        "narrative_available": narrative_available,
    }


def watch_items(incidents: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every stored watch, in the order the store already ranked them."""
    return [watch_item(incident) for incident in incidents if _is_watch(incident)]


def watch_item(incident: Mapping[str, Any]) -> dict[str, Any]:
    """One watch, copied field for field off the stored C3 record.

    Nothing here is derived. `projected_loss_per_hour` is the detector's own
    projected figure, kept under its stored name so the page cannot present it
    as realised money, and the two floor vectors are passed through whole so
    the page can say why this cohort is not yet an incident rather than
    deciding that for itself.
    """
    detection = _mapping(incident.get("detection"))
    watch = _mapping(detection.get("watch"))
    financial = _mapping(incident.get("financial_impact"))
    cohort = _mapping(incident.get("affected_cohort"))
    return {
        "incident_id": incident.get("incident_id"),
        "lifecycle_state": incident.get("lifecycle_state"),
        "severity": incident.get("severity"),
        "onset": incident.get("onset"),
        "affected_cohort": incident.get("affected_cohort"),
        "scope_label": cohort_scope_label(cohort),
        "change": incident.get("change"),
        "projected_loss_per_hour": financial.get("projected_loss_per_hour"),
        "reasons": list(watch.get("reasons") or []),
        "watch_floors": watch.get("watch_floors"),
        "not_yet_met": list(watch.get("not_yet_met") or []),
        "detection_floors": detection.get("detection_floors"),
        "trajectory": watch.get("trajectory"),
        "statement": watch.get("statement"),
        "leading_indicators": watch.get("leading_indicators"),
        "degraded_leading_indicators": list(watch.get("degraded_leading_indicators") or []),
    }


def detail(
    incident: Mapping[str, Any],
    investigation: Mapping[str, Any] | None = None,
    escalation_events: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full incident, investigation, evidence trail, and the six TAM questions."""
    outcome = None if investigation is None else investigation.get("outcome")
    narrative_available = _narrative_available(investigation)
    # The raw C4 result body still holds investigation/degrade.py's placeholder
    # text (e.g. "Causal investigation unavailable: ...") when the narrative
    # isn't available. Passing it through unfiltered here would show the
    # dashboard's confidence badge (surfaces/static/app.js reads this field
    # directly) as if it were a real diagnosis, even though the narrative
    # banner correctly says it is unavailable - the same leak fixed in
    # escalation.py:_payload for Slack/phone.
    result_body = _result_body(investigation) if narrative_available else None
    trail = [] if investigation is None else list(investigation.get("trail") or [])
    record = dict(incident)
    record["scope_label"] = cohort_scope_label(_mapping(incident.get("affected_cohort")))
    return {
        "incident": record,
        "investigation": {
            "outcome": outcome,
            "narrative_available": narrative_available,
            "result": result_body,
            "started_at": None if investigation is None else investigation.get("started_at"),
            "completed_at": None if investigation is None else investigation.get("completed_at"),
            "duration_ms": None if investigation is None else investigation.get("duration_ms"),
            "version": None if investigation is None else investigation.get("version"),
        },
        "evidence_trail": trail,
        "escalation": list(escalation_events or []),
        "questions": six_questions(incident, result_body, narrative_available),
    }


def six_questions(
    incident: Mapping[str, Any],
    result_body: Mapping[str, Any] | None,
    narrative_available: bool,
) -> dict[str, Any]:
    """PRD section 16. Questions 1-3 are incident facts; 4-6 are C4 narrative."""
    questions = {
        "what_changed": incident.get("change"),
        "where": incident.get("affected_cohort"),
        "how_much_it_matters": incident.get("financial_impact"),
        "what_probably_caused_it": None,
        "why_we_believe_that": None,
        "what_the_operator_should_do": None,
        "narrative_available": narrative_available,
    }
    if not narrative_available or not result_body:
        return questions
    questions["what_probably_caused_it"] = result_body.get("leading_hypothesis")
    questions["why_we_believe_that"] = {
        "confirmed_facts": result_body.get("confirmed_facts"),
        "supporting_evidence": result_body.get("supporting_evidence"),
        "competing_explanations": result_body.get("competing_explanations"),
        "why_ambiguity_exists": result_body.get("why_ambiguity_exists"),
        "missing_evidence": result_body.get("missing_evidence"),
        "diagnostic_confidence": result_body.get("diagnostic_confidence"),
    }
    questions["what_the_operator_should_do"] = result_body.get("recommended_next_action")
    return questions


def cohort_scope_label(cohort: Mapping[str, Any] | None) -> str:
    """User-visible scope taken only from dimensions the cohort actually names."""
    data = _mapping(cohort)
    merchant_id = _named(data.get("merchant_id"))
    if merchant_id is not None:
        return merchant_id
    parts: list[str] = []
    used: set[str] = set()
    for key in _SCOPE_ORDER:
        value = _named(data.get(key))
        if value is None:
            continue
        used.add(key)
        if key == "provider":
            parts.append(_readable_id(value))
        else:
            parts.append(f"{_SCOPE_NOUN[key]} {value}")
    for key, raw in data.items():
        if key in used or key == "merchant_id":
            continue
        value = _named(raw)
        if value is None:
            continue
        parts.append(f"{str(key).replace('_', ' ')} {value}")
    return " · ".join(parts) if parts else "Platform-wide"


def merchant_health(incidents: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-merchant view of stored incident severity and money.

    No health score is invented and nothing is added up across a group: the
    money published here is copied off that group's own highest-priority
    live record - or, where the group holds none, its highest-priority closed
    one, flagged `source_is_active: False` so the board reads it as history
    rather than as a loss still running. It travels with the id of the record
    it came from so the board can cite it. A group whose cohort names no
    merchant keeps `merchant_id` null, which is how the board knows not to
    call it a merchant.
    """
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for incident in incidents:
        if _is_watch_record(incident):
            continue
        grouped.setdefault(_health_group_key(incident), []).append(incident)
    health = []
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for key in sorted(grouped):
        # Active records rank ahead of closed ones, so a group with anything
        # live never publishes the money of an incident that is already over.
        # A group whose every record is closed is omitted entirely: publishing
        # its money under a calm headline made the board assert loss still at
        # risk after Clear (verify-demo beat no-stale-revenue).
        records = sorted(
            grouped[key],
            key=lambda item: (
                0 if _is_active(item) else 1,
                rank.get(str(item.get("severity", "")).lower(), 99),
                str(item.get("incident_id", "")),
            ),
        )
        active_count = sum(1 for item in records if _is_active(item))
        top = records[0]
        cohort = _mapping(top.get("affected_cohort"))
        source_active = _is_active(top)
        # Closed-only groups may still appear as history, but their money must
        # not travel: a calm board that still lists gmv_at_risk asserts a loss
        # that is no longer live (verify-demo beat no-stale-revenue).
        financial = top.get("financial_impact") if source_active else None
        change = top.get("change") if source_active else None
        health.append(
            {
                "merchant_id": _named(cohort.get("merchant_id")),
                "scope_label": cohort_scope_label(cohort),
                "highest_severity": top.get("severity"),
                "active_incident_count": active_count,
                "incident_ids": [item.get("incident_id") for item in records],
                "source_incident_id": top.get("incident_id"),
                "source_is_active": source_active,
                "financial_impact": financial,
                "change": change,
            }
        )
    return health


def _is_active(incident: Mapping[str, Any]) -> bool:
    state = _state(incident)
    return state != WATCHING_STATE and state not in INACTIVE_STATES


def _is_watch(incident: Mapping[str, Any]) -> bool:
    return _state(incident) == WATCHING_STATE


def _is_watch_record(incident: Mapping[str, Any]) -> bool:
    """Exclude an expired watch from incident cost history.

    Expiring a watch changes its lifecycle state to ``resolved`` in place, but
    keeps the detector's ``detection.watch`` explanation on the C3 record.
    That record never had realised incident money or a source incident for the
    history section. A promoted watch replaces that block when it becomes an
    incident, so it remains eligible here with its measured money.
    """
    detection = _mapping(incident.get("detection"))
    return _is_watch(incident) or isinstance(detection.get("watch"), Mapping)


def _state(incident: Mapping[str, Any]) -> str:
    return str(incident.get("lifecycle_state", "")).strip().lower()


def _narrative_available(investigation: Mapping[str, Any] | None) -> bool:
    if investigation is None:
        return False
    outcome = investigation.get("outcome")
    if not outcome:
        return False
    return outcome != "agent_unavailable"


def _result_body(investigation: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if investigation is None:
        return None
    body = investigation.get("result")
    return dict(body) if isinstance(body, Mapping) else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _named(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _readable_id(value: str) -> str:
    words = []
    for part in value.split("-"):
        if len(part) <= 3 and any(character.isdigit() for character in part):
            words.append(part.upper())
        else:
            words.append(part.capitalize())
    return " ".join(words)


def _health_group_key(incident: Mapping[str, Any]) -> tuple[Any, ...]:
    cohort = _mapping(incident.get("affected_cohort"))
    merchant_id = _named(cohort.get("merchant_id"))
    if merchant_id is not None:
        return ("merchant", merchant_id)
    dimensions = tuple(
        sorted(
            (str(key), str(value))
            for key, value in cohort.items()
            if key != "merchant_id" and _named(value) is not None
        )
    )
    return ("scope", dimensions)


SEVERITY_LADDER = ("low", "medium", "high", "critical")


def escalations(
    incidents: list[Mapping[str, Any]],
    recorded: Mapping[str, list[Mapping[str, Any]]],
    binding: Mapping[str, tuple[str, ...]],
    calls: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stored escalation outcomes per incident, plus the calls still pending."""
    groups = []
    for incident in incidents:
        if _is_watch(incident):
            continue
        incident_id = str(incident.get("incident_id", ""))
        events = list(recorded.get(incident_id) or [])
        payload = _mapping(events[0].get("payload")) if events else {}
        groups.append(
            {
                "incident_id": incident.get("incident_id"),
                "severity": incident.get("severity"),
                "lifecycle_state": incident.get("lifecycle_state"),
                "onset": incident.get("onset"),
                "scope_label": cohort_scope_label(_mapping(incident.get("affected_cohort"))),
                "affected_cohort": incident.get("affected_cohort"),
                "change": incident.get("change"),
                "financial_impact": incident.get("financial_impact"),
                "blast_radius": incident.get("blast_radius"),
                "expected_channels": list(binding.get(str(incident.get("severity", "")).lower(), ())),
                "channels": [
                    {
                        "channel": event.get("channel"),
                        "status": event.get("status"),
                        "detail": event.get("detail"),
                        "created_at": event.get("created_at"),
                    }
                    for event in events
                ],
                "payload": payload,
            }
        )
    return {
        "binding": [
            {"severity": severity, "channels": list(binding.get(severity, ()))}
            for severity in SEVERITY_LADDER
        ],
        "incidents": groups,
        "calls": list(calls or []),
    }
