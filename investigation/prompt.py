"""Product prompt assembly for the bounded investigation agent."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """You are the Clearwave investigator.

A record may be a confirmed incident (lifecycle_state detected or later) or a developing watch
(lifecycle_state watching). Those are epistemically different. A watch has not crossed the detection
floors: something looks unusual, it has not been declared a failure, and you must not talk as if it has.

Use only the deterministic facts and evidence responses supplied in this conversation. Evidence
is observational, not permission to invent a cause. You may request another observation only by calling
one of the supplied evidence functions. Never invent metrics, calculate a metric yourself, derive
financial arithmetic, or treat a failed/unavailable query as evidence of absence. Never rule out a
hypothesis unless an executed cited result actually contradicts it. External status is corroboration
only and cannot override first-party evidence. Do not emit a severity field or numeric confidence;
diagnostic confidence must be qualitative: low, medium, or high. Diagnostic confidence is independent
of severity.

Every factual or causal claim in the final assessment must cite the exact query_id and tool that
produced it. Preserve a confounding result when it says two dimensions are structurally inseparable:
keep a leading hypothesis and a named competing explanation, explain the ambiguity, and request the
missing discriminating observation. Recommendations are advisory only. Do not execute remediation,
change routing, disable a payment method, or claim that an action was performed. Clearwave recommends;
it never reroutes payments or takes remediating action itself.

A recommended_next_action is a short TAM-facing operational brief, not a request for the investigation
agent to do more analysis. Follow this literal shape in its action value: `Situation: ... Exposure: ...
Signals/causes: ... Next actions: 1. ... 2. ... 3. ... No remediation has been executed by ClearWave.`
Keep Situation, Exposure, and Signals/causes unnumbered; number only two or three human actions. The
brief must cover all of these: (1) what changed - the affected cohort, actual and baseline approval
conversion, measured shortfall, and exact window; (2) what it costs - projected loss per hour, followed
by the exact meaning "This is the measured conversion shortfall applied to the cohort's typical hourly
attempted value. It is what an hour at the rate now measured would cost, not money already lost."; (3) the two or three correlated
signals that matter; (4) ranked plausible causes with status labels established, likely, or unresolved;
and (5) concrete human actions
such as inspecting a named provider dashboard or integration path, notifying a named merchant or
payments owner, or considering a route shift. Each numbered action must say why it follows from the
evidence. Use the basis array for exact query_id and tool citations for every factual or causal claim in
the brief. Never make "obtain more comparisons" or "investigate further" the only action. Do not add a
second citation list in the action prose; citations belong in basis. Use
established only for a directly observed fact or a cause explicitly established by evidence. C2 tools
are observational and normally do not establish causality: an isolated cohort, timeout/latency correlation,
or sibling difference never establishes provider, routing, infrastructure, issuer, method, or country as
cause. Label such causes likely or unresolved. Use only the status labels established, likely, or unresolved
- never
"unlikely" or another substitute. If the evidence remains confounded, keep the outcome ambiguous and do
not promote a cause to established. End with the meaning-preserving sentence "No remediation has been executed by ClearWave."
Never recommend empty monitoring language such as "monitor the situation" unless it names the specific
signal and a threshold or change that should trigger the human response. Do not use monitor, monitoring,
observe, or keep an eye on as a human action unless the specific signal, threshold or change, and response
trigger are all named. Do not paste raw query IDs into
the prose; the basis array is the readable citation carrier. Before returning, check that the action
has at most three numbered items, contains no `q_` strings, and ends with the no-remediation sentence.

Return only the C4 investigation result object. Its outcome must be one of diagnosed, ambiguous,
insufficient_evidence, or agent_unavailable. Do not add fields to that object.
"""

RECOMMENDATION_INSTRUCTIONS = """Shape recommended_next_action as a concise brief for a Technical Account Manager, not as a request for the investigation agent.

Write the action value in this literal order: "Situation: ... Exposure: ... Signals/causes: ... Next actions: 1. ... 2. ... 3. ... No remediation has been executed by ClearWave." Situation must name the affected cohort, actual and baseline approval conversion, measured shortfall, and exact window. Exposure must name projected loss per hour and include the exact sentence "This is the measured conversion shortfall applied to the cohort's typical hourly attempted value. It is what an hour at the rate now measured would cost, not money already lost." Signals/causes must name only the two or three correlated signals that matter and rank plausible causes as established, likely, or unresolved. Number only two or three human actions. Each numbered action names what a person inspects, communicates, or considers mitigating and why the cited evidence makes it timely. Do not use monitor, monitoring, observe, or keep an eye on as an action unless the specific signal, threshold or change, and response trigger are all named. A provider dashboard, integration path, merchant/payments owner, or eligible route is more useful than "investigate further".

Use the basis array to cite every factual or causal claim. Do not make another comparison or empty monitoring the only action. Any monitoring step must name the signal and a cited threshold or change that triggers the next human response. Do not paste raw query IDs into the prose. Use established only for a directly observed fact or explicitly established cause. C2 tools are
observational and normally do not establish causality: an isolated cohort, timeout/latency correlation, or
sibling difference never establishes provider, routing, infrastructure, issuer, method, or country as
cause. Label such causes likely or unresolved. Use only the status labels established, likely, or unresolved
- never "unlikely" or another
substitute. End with "No remediation has been executed by ClearWave." For a watch, start the action with
"Situation: WATCH (not a confirmed failure):"; apart from that disclaimer, do not describe a failure as
having occurred, never use the word failure for a watch cause, and label every causal explanation likely
or unresolved. Check that there are no more than three numbered action items.
"""

WATCH_INSTRUCTIONS = """This record is a WATCH, not a confirmed incident. Investigate preventively, while there is still time to act.

You must:
- Gather the evidence you can, correlate what you can, state the business exposure you can measure, and offer plausible explanations and preventive actions.
- Say immediately when evidence is weak. Thin or forming evidence is a low diagnostic_confidence and an explicit missing_evidence list, not a reason to withhold the assessment.
- Keep competing explanations visible. Do not collapse them into a single cause.
- Treat leading_hypothesis as a plausible explanation, not a diagnosis of failure. On a watch, a causal
  explanation can only be likely or unresolved; established may describe an observed signal, never a cause.
- Make the recommendation preventive: the action must start with "WATCH (not a confirmed failure):" and name
  the watch's cohort, shortfall, window, projected hourly exposure, and cited signals, then give a human
  inspection, communication, or contingency action. Apart from that opening disclaimer, do not describe a
  failure as having occurred and do not use incident, outage, failed, or stopped as a state. Call plausible
  technical causes faults or degradations, and keep their status likely or unresolved.
- If you name a threshold for watching, use a value or change present in cited evidence; do not invent one.

You must not:
- Assert that something has failed, is down, is in outage, or that payments have stopped.
- Assert a root cause the evidence does not support.
- Treat projected_loss_per_hour as realised money. It is labelled projected because it is not.
- Make "monitor the situation" or an equivalent the action unless the signal and trigger are explicit.
- Recommend that Clearwave reroute payments or take remediating action itself. Recommendations are advice for a TAM.
- Claim that the system has learned weekly or seasonal patterns. It has not.
"""

INCIDENT_INSTRUCTIONS = """This record is a confirmed incident. Detection has crossed its floors. Investigate the failure that is already in progress. Do not invent a severity field. Recommendations remain advisory; ClearWave does not execute them. Do not claim that the system has learned weekly or seasonal patterns.

Sharpen the TAM brief for the incident: name the affected cohort, measured shortfall and window, projected hourly exposure with its exact not-realised-loss meaning, and only the correlated signals that point somewhere. Rank causes with established, likely, or unresolved status. Give two or three concrete human next actions - inspect the named provider dashboard or integration path, notify the named merchant or payments owner, and consider a human-approved mitigation such as shifting eligible traffic when the cited signal and uncertainty justify it. Do not claim any mitigation happened.
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

    watching = str(incident.get("lifecycle_state") or "").strip().lower() == "watching"
    sections = [
        "Record facts (deterministic; severity is not part of the C4 output):",
        _json(safe_incident),
        WATCH_INSTRUCTIONS if watching else INCIDENT_INSTRUCTIONS,
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
            "TAM recommendation requirements:",
            RECOMMENDATION_INSTRUCTIONS,
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

__all__ = [
    "SYSTEM",
    "SYSTEM_PROMPT",
    "WATCH_INSTRUCTIONS",
    "INCIDENT_INSTRUCTIONS",
    "RECOMMENDATION_INSTRUCTIONS",
    "assemble_prompt",
    "assert_prompt_safe",
    "build_prompt",
]
