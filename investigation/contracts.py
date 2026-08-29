"""Pydantic models for the C4 investigation result contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


Outcome = Literal[
    "diagnosed",
    "ambiguous",
    "insufficient_evidence",
    "agent_unavailable",
]
DiagnosticConfidence = Literal["low", "medium", "high"]
EvidenceTool = Literal[
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
    "metric_series",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceItem(ContractModel):
    """One claim tied to the exact gateway query that produced its evidence."""

    claim: StrictStr = Field(min_length=1)
    query_id: StrictStr = Field(min_length=1)
    tool: EvidenceTool


class ConfirmedFact(ContractModel):
    statement: StrictStr = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class LeadingHypothesis(ContractModel):
    statement: StrictStr = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CompetingExplanation(ContractModel):
    explanation: StrictStr = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class AmbiguityExplanation(ContractModel):
    statement: StrictStr = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class MissingEvidence(ContractModel):
    request: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class RecommendedNextAction(ContractModel):
    action: StrictStr = Field(min_length=1)
    urgency: StrictStr = Field(min_length=1)
    basis: list[EvidenceItem] = Field(default_factory=list)


class InvestigationResult(ContractModel):
    """The stable C4 assessment. Severity is intentionally not a field here."""

    incident_id: StrictStr = Field(min_length=1)
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    leading_hypothesis: LeadingHypothesis
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    competing_explanations: list[CompetingExplanation] = Field(default_factory=list)
    why_ambiguity_exists: AmbiguityExplanation
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    diagnostic_confidence: DiagnosticConfidence
    recommended_next_action: RecommendedNextAction
    outcome: Outcome = "diagnosed"

    @model_validator(mode="after")
    def incident_id_is_present(self) -> "InvestigationResult":
        if not self.incident_id.strip():
            raise ValueError("incident_id must not be blank")
        return self


def result_dict(result: InvestigationResult | dict[str, Any]) -> dict[str, Any]:
    """Return JSON-compatible C4 data without adding runtime metadata."""
    if isinstance(result, InvestigationResult):
        return result.model_dump(mode="json", exclude_none=False)
    return InvestigationResult.model_validate(result).model_dump(mode="json", exclude_none=False)


__all__ = [
    "AmbiguityExplanation",
    "CompetingExplanation",
    "ConfirmedFact",
    "DiagnosticConfidence",
    "EvidenceItem",
    "EvidenceTool",
    "InvestigationResult",
    "LeadingHypothesis",
    "MissingEvidence",
    "Outcome",
    "RecommendedNextAction",
    "result_dict",
]
