"""Product prompt assembly for the bounded investigation agent."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """You are the Clearwave incident investigator.

Use only the deterministic incident facts and evidence responses supplied in this conversation. Evidence
is observational, not permission to invent a cause. You may request another observation only by calling
one of the supplied evidence functions. Never invent metrics, calculate a metric yourself, derive
financial arithmetic, or treat a failed/unavailable query as evidence of absence. Never rule out a
hypothesis unless an executed cited result actually contradicts it. External status is corroboration
only and cannot override first-party evidence. Do not emit a severity field or numeric confidence;
diagnostic confidence must be qualitative: low, medium, or high.

Every factual or causal claim in the final assessment must cite the exact query_id and tool that
produced it. Preserve a confounding result when it says two dimensions are structurally inseparable:
keep a leading hypothesis and a named competing explanation, explain the ambiguity, and request the
missing discriminating observation. Recommendations are advisory only. Do not execute remediation,
change routing, disable a payment method, or claim that an action was performed.

Return only the C4 investigation result object. Its outcome must be one of diagnosed, ambiguous,
insufficient_evidence, or agent_unavailable. Do not add fields to that object.
"""

_PUBLIC_INCIDENT_FIELDS = (
    "incident_id",
    "affected_cohort",
    "change",
    "onset",
    "persistence",
    "blast_radius",
    "financial_impact",
    "severity",
    "lifecycle_state",
)
_SENSITIVE_KEYS = {
    "scenario",
    "scenario_id",
    "scenario_identifier",
    "hidden_truth",
    "ground_truth",
    "evaluator",
}
_DISCRIMINATORS = {
    "provider": "Compare the affected provider with a sibling provider while holding the other observed dimensions constant.",
    "issuer": "Compare the issuer through another provider, or compare another issuer through the affected provider.",
    "payment_method": "Compare the affected payment method with another method in the same merchant and country.",
    "country": "Compare the affected method/provider in another country for the same merchant.",
    "retry": "Compare retry depth, queue trajectory, and attempt-level conversion with a healthy sibling.",
    "queue": "Compare queue depth and delay trajectory with a healthy sibling cohort.",
    "application": "Correlate service/runtime health and deployment identity with the payment symptoms.",
    "infrastructure": "Correlate runtime health, latency, queue depth, and service health with a healthy sibling.",
    "deployment": "Compare deployment timing and service health with a cohort not on the deployment.",
}


def assemble_prompt(
    incident: Mapping[str, Any],
    opening_evidence: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    domain_root: Path | str | None = None,
) -> str:
    """Build model input from public C3/C2 data and the surviving hypotheses."""
    candidate_list = (
        candidates.get("candidates", [])
        if isinstance(candidates, Mapping)
        else list(candidates)
    )
    safe_incident = {
        key: _safe_value(incident.get(key))
        for key in _PUBLIC_INCIDENT_FIELDS
        if key in incident
    }
    safe_evidence = _safe_value(dict(opening_evidence))
    safe_candidates = _safe_value(list(candidate_list))
    domain_files = _domain_files(domain_root)
    domain_context = _domain_context(domain_files, safe_candidates)
    discriminators = _relevant_discriminators(safe_candidates)

    sections = [
        "Incident facts (deterministic; severity is not part of the C4 output):",
        _json(safe_incident),
        "Opening evidence responses (gateway-issued query_id values are the only citations):",
        _json(safe_evidence),
        "Surviving deterministic pre-filter hypotheses:",
        _json(safe_candidates),
        "Discriminating observations relevant to those hypotheses:",
        _json(discriminators),
    ]
    if domain_context:
        sections.extend(["Standing domain context:", domain_context])
    sections.extend(
        [
            "Investigate through the evidence functions when a supplied observation does not discriminate.",
            "Then return the C4 result only, with an outcome and evidence citations on every claim.",
        ]
    )
    prompt = "\n\n".join(sections)
    assert_prompt_safe(prompt)
    return prompt


build_prompt = assemble_prompt


def assert_prompt_safe(prompt: str) -> None:
    """Hard-assert that quarantined runtime inputs did not enter model input."""
    lowered = prompt.lower()
    for key in _SENSITIVE_KEYS:
        assert f'"{key}"' not in lowered, f"sensitive key {key!r} entered investigation prompt"
        assert f"{key}:" not in lowered, f"sensitive key {key!r} entered investigation prompt"
    for phrase in ("scenario identifier", "hidden truth", "ground truth", "evaluator"):
        assert phrase not in lowered, f"quarantined material {phrase!r} entered investigation prompt"
    assert "evaluator/" not in lowered
    assert "evaluator\\" not in lowered


def _domain_files(root: Path | str | None) -> list[Path]:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "docs" / "domain"
    if not base.is_dir():
        return []
    return sorted(path for path in base.rglob("*.md") if path.is_file())


def _domain_context(files: Iterable[Path], candidates: Any) -> str:
    if not files:
        return ""
    relevant = _relevant_discriminators(candidates)
    chunks: list[str] = []
    for path in files:
        name = path.name.lower()
        if name == "readme.md" or any(token in name for token in ("scenario", "truth", "evaluator")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text:
            continue
        if "failure" in name or "decline" in name or "taxonomy" in name:
            chunks.append(f"[{path.name}]\n{text}")
        elif relevant:
            chunks.append(f"[{path.name}]\n{text}")
    return "\n\n".join(chunks)


def _relevant_discriminators(candidates: Any) -> dict[str, str]:
    text = " ".join(
        str(candidate.get("hypothesis", candidate.get("name", "")))
        for candidate in candidates or ()
        if isinstance(candidate, Mapping)
    ).lower()
    result: dict[str, str] = {}
    for keyword, discriminator in _DISCRIMINATORS.items():
        if keyword in text:
            result[keyword] = discriminator
    if not result:
        result["general"] = "Collect a cross-dimension comparison that separates the leading candidate from its alternatives."
    return result


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if str(key).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, default=str)


SYSTEM = SYSTEM_PROMPT

__all__ = ["SYSTEM", "SYSTEM_PROMPT", "assemble_prompt", "assert_prompt_safe", "build_prompt"]
