"""A hard-guarded hypothesis ledger for bounded investigations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

LEGAL_STATUSES = frozenset({"supported", "contradicted", "cannot_distinguish"})


class LedgerError(ValueError):
    """Raised when an illegal or unsupported ledger transition is requested."""


@dataclass
class HypothesisEntry:
    hypothesis: str
    status: str = "supported"
    citations: list[str] = field(default_factory=list)
    discriminating_observation: str = ""
    score: float | None = None

    def __post_init__(self) -> None:
        if not self.hypothesis.strip():
            raise LedgerError("hypothesis must not be blank")
        if self.status not in LEGAL_STATUSES:
            raise LedgerError(f"status must be one of {sorted(LEGAL_STATUSES)}")
        self.citations = [str(citation) for citation in self.citations if str(citation)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "status": self.status,
            "citations": list(self.citations),
            "discriminating_observation": self.discriminating_observation,
            **({"score": self.score} if self.score is not None else {}),
        }


class HypothesisLedger:
    """Keep candidate causes and prevent unsupported ruled-out claims.

    The ledger is seeded by deterministic pre-filter output. A contradiction is
    accepted only when a cited, executed gateway response contains a
    contradiction marker or a deterministic signal that is incompatible with
    the candidate. Missing evidence is never treated as a contradiction.
    """

    def __init__(
        self,
        entries: Iterable[HypothesisEntry | Mapping[str, Any]] = (),
        *,
        trail: Iterable[Mapping[str, Any]] | Any | None = None,
    ) -> None:
        self._entries: dict[str, HypothesisEntry] = {}
        self.trail = trail
        for entry in entries:
            self.add(entry)

    @classmethod
    def from_prefilter(
        cls,
        prefilter_result: Mapping[str, Any] | Iterable[Mapping[str, Any]],
        *,
        trail: Iterable[Mapping[str, Any]] | Any | None = None,
    ) -> "HypothesisLedger":
        candidates = (
            prefilter_result.get("candidates", [])
            if isinstance(prefilter_result, Mapping)
            else prefilter_result
        )
        entries = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            name = str(candidate.get("hypothesis") or candidate.get("name") or "").strip()
            if not name:
                continue
            entries.append(
                HypothesisEntry(
                    hypothesis=name,
                    status="supported",
                    discriminating_observation=_discriminator_for(name),
                    score=_number(candidate.get("score")),
                )
            )
        if not entries:
            entries.append(
                HypothesisEntry(
                    hypothesis="unknown_observable_failure",
                    status="cannot_distinguish",
                    discriminating_observation="Collect a comparison that separates the remaining observable dimensions.",
                )
            )
        return cls(entries, trail=trail)

    seed = from_prefilter

    @property
    def entries(self) -> list[HypothesisEntry]:
        return list(self._entries.values())

    def add(self, entry: HypothesisEntry | Mapping[str, Any]) -> HypothesisEntry:
        candidate = (
            entry
            if isinstance(entry, HypothesisEntry)
            else HypothesisEntry(
                hypothesis=str(entry.get("hypothesis") or entry.get("name") or ""),
                status=str(entry.get("status", "supported")),
                citations=list(entry.get("citations", [])),
                discriminating_observation=str(
                    entry.get("discriminating_observation", "")
                ),
                score=_number(entry.get("score")),
            )
        )
        if candidate.hypothesis in self._entries:
            raise LedgerError(f"duplicate hypothesis: {candidate.hypothesis}")
        if candidate.status == "contradicted":
            if not candidate.citations or not any(
                self._citation_contradicts(candidate.hypothesis, query_id)
                for query_id in candidate.citations
            ):
                raise LedgerError(
                    f"cannot seed contradicted hypothesis {candidate.hypothesis!r} without a contradicting citation"
                )
        self._entries[candidate.hypothesis] = candidate
        return candidate

    def get(self, hypothesis: str) -> HypothesisEntry:
        try:
            return self._entries[hypothesis]
        except KeyError as exc:
            raise LedgerError(f"unknown hypothesis: {hypothesis}") from exc

    def update(
        self,
        hypothesis: str,
        status: str,
        citations: Iterable[str | Mapping[str, Any]] = (),
    ) -> HypothesisEntry:
        """Move an entry, requiring actual contradictory evidence when needed."""
        if status not in LEGAL_STATUSES:
            raise LedgerError(f"status must be one of {sorted(LEGAL_STATUSES)}")
        entry = self.get(hypothesis)
        query_ids = [_citation_id(citation) for citation in citations]
        query_ids = [query_id for query_id in query_ids if query_id]
        if status == "contradicted":
            if not query_ids:
                raise LedgerError(
                    f"cannot contradict {hypothesis!r} without a contradicting citation"
                )
            if not any(self._citation_contradicts(entry.hypothesis, query_id) for query_id in query_ids):
                raise LedgerError(
                    f"citations do not contradict hypothesis {hypothesis!r}"
                )
        entry.status = status
        entry.citations = query_ids
        return entry

    mark = update
    set_status = update

    def mark_contradicted(
        self, hypothesis: str, citations: Iterable[str | Mapping[str, Any]]
    ) -> HypothesisEntry:
        return self.update(hypothesis, "contradicted", citations)

    def mark_supported(
        self, hypothesis: str, citations: Iterable[str | Mapping[str, Any]] = ()
    ) -> HypothesisEntry:
        return self.update(hypothesis, "supported", citations)

    def mark_cannot_distinguish(
        self, hypothesis: str, citations: Iterable[str | Mapping[str, Any]] = ()
    ) -> HypothesisEntry:
        return self.update(hypothesis, "cannot_distinguish", citations)

    def as_dict(self) -> list[dict[str, Any]]:
        return [entry.as_dict() for entry in self.entries]

    to_dict = as_dict

    def context(self) -> dict[str, Any]:
        return {
            "candidates": self.as_dict(),
            "rule": "Only an executed citation whose result contradicts a hypothesis may mark it contradicted.",
        }

    def _citation_contradicts(self, hypothesis: str, query_id: str) -> bool:
        entry = _trail_entry(self.trail, query_id)
        if entry is None or not entry.get("executed", True):
            return False
        response = entry.get("response", {})
        if not isinstance(response, Mapping):
            return False
        if response.get("contradicts") is True:
            return True
        names = response.get("contradicts_hypotheses", [])
        if isinstance(names, Iterable) and not isinstance(names, (str, bytes)):
            if hypothesis in names:
                return True
        return _deterministically_contradicts(hypothesis, entry.get("tool", ""), response)


def citation_contradicts(
    hypothesis: str,
    citation: str | Mapping[str, Any],
    trail: Iterable[Mapping[str, Any]] | Any,
) -> bool:
    """Public predicate used by validators and tests for the ledger guard."""
    query_id = _citation_id(citation)
    entry = _trail_entry(trail, query_id)
    if entry is None or not entry.get("executed", True):
        return False
    response = entry.get("response", {})
    if not isinstance(response, Mapping):
        return False
    names = response.get("contradicts_hypotheses", [])
    explicitly_contradicts = (
        isinstance(names, str) and names == hypothesis
    ) or (
        isinstance(names, Iterable)
        and not isinstance(names, (str, bytes))
        and hypothesis in names
    )
    return response.get("contradicts") is True or explicitly_contradicts or _deterministically_contradicts(
        hypothesis, entry.get("tool", ""), response
    )


def _deterministically_contradicts(
    hypothesis: str, tool: str, response: Mapping[str, Any]
) -> bool:
    """Recognise only direct, tool-shaped counter-observations.

    This deliberately does not infer contradiction from an absent field or a
    failed query. C2 responses may also opt into an explicit contradiction
    marker for a registered failure mode.
    """
    name = hypothesis.lower().replace("-", "_").replace(" ", "_")
    operational = _mapping(response)
    health = _mapping(operational.get("service_health")).get("status")
    runtime = _mapping(operational.get("runtime_health")).get("status")
    timeout = _number(operational.get("timeout_rate"))
    error = _number(operational.get("error_rate"))
    latency = _mapping(operational.get("latency_ms"))
    p95 = _number(latency.get("p95"))

    if "provider" in name and "operational" in tool:
        return (
            health in {"healthy", "operational"}
            and (timeout is None or timeout < 0.10)
            and (error is None or error < 0.05)
            and (p95 is None or p95 < 1000)
        )
    if "issuer" in name and "decline" in name and "decline" in tool:
        return _shift(response, "issuer_decline") <= 0
    if ("application" in name or "deployment" in name) and "operational" in tool:
        return runtime in {"healthy", "operational"} and error is not None and error < 0.05
    if ("infrastructure" in name or "queue" in name) and "retry" in tool:
        queue = _mapping(response.get("queue"))
        return (
            _number(queue.get("depth_end")) is not None
            and _number(queue.get("depth_start")) is not None
            and _number(queue.get("depth_end")) <= _number(queue.get("depth_start"))
        )
    if "retry" in name and "retry" in tool:
        factor = _number(response.get("retry_amplification_factor"))
        return factor is not None and factor <= 1.10
    return False


def _discriminator_for(hypothesis: str) -> str:
    name = hypothesis.lower()
    if "provider" in name:
        return "Compare the affected provider with a sibling provider on the same issuer and cohort."
    if "issuer" in name or "bank" in name:
        return "Compare the issuer across another provider while holding the merchant and time window constant."
    if "payment_method" in name or "method" in name:
        return "Compare this payment method with another method in the same country and provider."
    if "country" in name:
        return "Compare the payment method in another country for the same merchant and provider."
    if "retry" in name or "queue" in name:
        return "Compare retry depth and queue trajectory with a healthy sibling cohort."
    if "application" in name or "deployment" in name or "infrastructure" in name:
        return "Correlate runtime, deployment, and service observations with a healthy sibling."
    return "Collect a cross-dimension comparison that separates this candidate from its alternatives."


def _trail_entry(trail: Any, query_id: str) -> Mapping[str, Any] | None:
    if not query_id or trail is None:
        return None
    entries = getattr(trail, "entries", trail)
    for entry in entries or ():
        if isinstance(entry, Mapping) and entry.get("query_id") == query_id:
            return entry
    return None


def _citation_id(citation: str | Mapping[str, Any]) -> str:
    if isinstance(citation, Mapping):
        return str(citation.get("query_id", ""))
    return str(citation)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _shift(response: Mapping[str, Any], reason: str) -> float:
    for item in response.get("reasons", ()):
        if isinstance(item, Mapping) and item.get("reason") == reason:
            value = _number(item.get("shift"))
            if value is not None:
                return value
    return 0.0


__all__ = [
    "LEGAL_STATUSES",
    "HypothesisEntry",
    "HypothesisLedger",
    "LedgerError",
    "citation_contradicts",
]
