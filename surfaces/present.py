"""Shape stored C3/C4 records for the dashboard. No domain arithmetic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

INACTIVE_STATES = {"resolved", "mitigated"}

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
    """Business overview figures, copied from the highest-priority incident."""
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
        ]
    }


def queue_item(
    incident: Mapping[str, Any],
    investigation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Severity and diagnostic confidence travel as independent fields."""
    result_body = _result_body(investigation)
    confidence = result_body.get("diagnostic_confidence") if result_body else None
    return {
        "incident_id": incident.get("incident_id"),
        "severity": incident.get("severity"),
        "diagnostic_confidence": confidence,
        "lifecycle_state": incident.get("lifecycle_state"),
        "outcome": None if investigation is None else investigation.get("outcome"),
        "onset": incident.get("onset"),
        "affected_cohort": incident.get("affected_cohort"),
        "change": incident.get("change"),
        "financial_impact": incident.get("financial_impact"),
        "narrative_available": _narrative_available(investigation),
    }


def detail(
    incident: Mapping[str, Any],
    investigation: Mapping[str, Any] | None = None,
    escalation_events: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full incident, investigation, evidence trail, and the six TAM questions."""
    result_body = _result_body(investigation)
    outcome = None if investigation is None else investigation.get("outcome")
    narrative_available = _narrative_available(investigation)
    trail = [] if investigation is None else list(investigation.get("trail") or [])
    return {
        "incident": dict(incident),
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
    """Per-merchant view of stored incident severity. No health score is invented."""
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for incident in incidents:
        grouped.setdefault(_health_group_key(incident), []).append(incident)
    health = []
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for key in sorted(grouped):
        records = sorted(
            grouped[key],
            key=lambda item: (
                rank.get(str(item.get("severity", "")).lower(), 99),
                str(item.get("incident_id", "")),
            ),
        )
        top = records[0]
        cohort = _mapping(top.get("affected_cohort"))
        health.append(
            {
                "merchant_id": _named(cohort.get("merchant_id")),
                "scope_label": cohort_scope_label(cohort),
                "highest_severity": top.get("severity"),
                "active_incident_count": sum(1 for item in records if _is_active(item)),
                "incident_ids": [item.get("incident_id") for item in records],
            }
        )
    return health


def _is_active(incident: Mapping[str, Any]) -> bool:
    return str(incident.get("lifecycle_state", "")).lower() not in INACTIVE_STATES


def _narrative_available(investigation: Mapping[str, Any] | None) -> bool:
    if investigation is None:
        return False
    return investigation.get("outcome") != "agent_unavailable"


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
