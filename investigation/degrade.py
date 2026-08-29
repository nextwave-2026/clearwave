"""Visible, deterministic degradation for unavailable investigation agents."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import (
    AmbiguityExplanation,
    CompetingExplanation,
    ConfirmedFact,
    EvidenceItem,
    InvestigationResult,
    LeadingHypothesis,
    MissingEvidence,
    RecommendedNextAction,
)

_ALLOWED_TOOLS = {
    "cohort_metrics",
    "cohort_compare",
    "drilldown",
    "decline_breakdown",
    "retry_stats",
    "operational_metrics",
    "confounding_check",
    "incident_history",
    "external_status",
    "financial_impact",
}


def degrade_result(
    incident: Mapping[str, Any],
    opening_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    trail: Iterable[Mapping[str, Any]] | Any | None = None,
    *,
    reason: str = "The investigation agent is unavailable.",
) -> InvestigationResult:
    """Return C4 data while retaining deterministic incident observations."""
    references = _references(opening_evidence, trail)
    incident_id = str(incident.get("incident_id", "unknown-incident"))
    deterministic = {
        key: incident.get(key)
        for key in (
            "affected_cohort",
            "change",
            "onset",
            "persistence",
            "blast_radius",
            "financial_impact",
            "severity",
        )
        if key in incident
    }
    fact_evidence = references[:1]
    facts = []
    if fact_evidence:
        facts.append(
            ConfirmedFact(
                statement="Deterministic incident facts were retained; the causal narrative is unavailable: "
                + json.dumps(deterministic, sort_keys=True, default=str),
                evidence=fact_evidence,
            )
        )
    evidence = references
    return InvestigationResult(
        incident_id=incident_id,
        confirmed_facts=facts,
        leading_hypothesis=LeadingHypothesis(
            statement=f"Causal investigation unavailable: {reason}",
            evidence=fact_evidence,
        ),
        supporting_evidence=evidence,
        competing_explanations=(
            [
                CompetingExplanation(
                    explanation="Potential causes remain unresolved because no agent assessment was available.",
                    evidence=fact_evidence,
                )
            ]
            if fact_evidence
            else []
        ),
        why_ambiguity_exists=AmbiguityExplanation(
            statement="No causal conclusion can be made while the investigation narrative is unavailable.",
            evidence=fact_evidence,
        ),
        missing_evidence=(
            [
                MissingEvidence(
                    request="Retry the bounded investigation and inspect the retained evidence trail.",
                    reason="A causal assessment could not be produced by the unavailable agent.",
                    evidence=fact_evidence,
                )
            ]
            if fact_evidence
            else []
        ),
        diagnostic_confidence="low",
        recommended_next_action=RecommendedNextAction(
            action="Review the deterministic incident facts and evidence trail; do not execute automatic remediation.",
            urgency="now",
            basis=fact_evidence,
        ),
        outcome="agent_unavailable",
    )


def _references(
    opening_evidence: Mapping[str, Mapping[str, Any]] | None,
    trail: Iterable[Mapping[str, Any]] | Any | None,
) -> list[EvidenceItem]:
    entries = list(getattr(trail, "entries", trail) or ())
    by_id = {
        str(entry.get("query_id")): entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("query_id")
    }
    values = list(opening_evidence.items()) if opening_evidence else []
    for tool_name, response in values:
        if isinstance(response, Mapping) and response.get("query_id"):
            by_id.setdefault(
                str(response["query_id"]),
                {"response": response, "tool": str(tool_name)},
            )
    result: list[EvidenceItem] = []
    for query_id, entry in by_id.items():
        tool = str(entry.get("tool", ""))
        if tool not in _ALLOWED_TOOLS:
            tool = _tool(entry.get("response", {}))
        if tool not in _ALLOWED_TOOLS:
            continue
        result.append(
            EvidenceItem(
                claim=f"The {tool} evidence response was retained in the trail.",
                query_id=query_id,
                tool=tool,
            )
        )
    return result


def _tool(response: Any) -> str:
    return str(response.get("tool", "")) if isinstance(response, Mapping) else ""


unavailable_result = degrade_result

__all__ = ["degrade_result", "unavailable_result"]
