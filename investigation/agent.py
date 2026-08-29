"""Bounded OpenAI Responses API investigation loop."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .contracts import InvestigationResult, result_dict
from .degrade import degrade_result
from .env import (
    openai_client_kwargs,
    openai_max_output_tokens,
    openai_model,
    openai_reasoning_effort,
    redact_secrets,
)
from .gateway import ALLOWED_TOOLS, EvidenceGateway
from .ledger import HypothesisLedger
from .prefilter import prefilter
from .prompt import SYSTEM_PROMPT, assemble_prompt, assert_prompt_safe
from .trail import EvidenceTrail

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MAX_OUTPUT_TOKENS = 6000
DEFAULT_MAX_TURNS = 6
DEFAULT_TIMEOUT_SECONDS = 30.0

_TOOL_DESCRIPTIONS = {
    "cohort_metrics": "Measure payment-level and attempt-level conversion for a cohort.",
    "cohort_compare": "Compare a cohort with sibling and parent cohorts.",
    "drilldown": "Read the deterministic incident localisation path.",
    "decline_breakdown": "Measure normalized decline reason shares and baseline shifts.",
    "retry_stats": "Measure retry depth, amplification, queue depth, and delay.",
    "operational_metrics": "Measure latency, errors, timeouts, health, and deployment identity.",
    "confounding_check": "Test whether two dimensions are structurally inseparable.",
    "incident_history": "Read prior incidents for a merchant or filtered cohort.",
    "external_status": "Read optional external provider corroboration.",
    "financial_impact": "Read deterministic financial impact for an incident.",
    "metric_series": "Read one named metric for a cohort over ordered event-time buckets.",
}
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": tool,
        "description": _TOOL_DESCRIPTIONS[tool],
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
    }
    for tool in ALLOWED_TOOLS
]


class InvestigationTimeout(TimeoutError):
    """Raised internally when a model or gateway call exceeds its deadline."""


@dataclass
class InvestigationRun(Mapping[str, Any]):
    """Result plus the product trail and timing needed by the runner."""

    result: InvestigationResult
    trail: EvidenceTrail
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: float | None = None

    @property
    def outcome(self) -> str:
        return self.result.outcome

    @property
    def result_dict(self) -> dict[str, Any]:
        return result_dict(self.result)

    def as_dict(self) -> dict[str, Any]:
        return self.result_dict

    def __getitem__(self, key: str) -> Any:
        return self.result_dict[key]

    def __iter__(self):
        return iter(self.result_dict)

    def __len__(self) -> int:
        return len(self.result_dict)


class InvestigationAgent:
    """Run one C3 incident through bounded evidence gathering and C4 validation."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        query_budget: int = 6,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        turn_budget: int | None = None,
        wall_clock_timeout: float | None = None,
    ) -> None:
        if turn_budget is not None:
            max_turns = turn_budget
        if wall_clock_timeout is not None:
            timeout_seconds = wall_clock_timeout
        if max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.client = client
        self.model = model or openai_model(DEFAULT_MODEL)
        self.max_output_tokens = openai_max_output_tokens(DEFAULT_MAX_OUTPUT_TOKENS)
        self.reasoning_effort = openai_reasoning_effort(DEFAULT_REASONING_EFFORT)
        self._reasoning_supported = _reasoning_support(self.model)
        self.max_turns = int(max_turns)
        self.query_budget = int(query_budget)
        self.timeout_seconds = float(timeout_seconds)

    def investigate(
        self,
        incident: Mapping[str, Any],
        gateway: EvidenceGateway | None = None,
    ) -> InvestigationRun:
        """Investigate an incident, always returning a visible result."""
        started_clock = time.monotonic()
        started_at = _utc_now()
        evidence_gateway = gateway or EvidenceGateway(query_budget=self.query_budget)
        opening: dict[str, dict[str, Any]] = {}
        try:
            opening = self._bounded(
                lambda: evidence_gateway.opening_bundle(incident),
                self._remaining(started_clock),
            )
        except InvestigationTimeout as exc:
            return self._finish_degraded(
                incident,
                opening,
                evidence_gateway,
                started_clock,
                started_at,
                f"The investigation deadline expired before opening evidence completed: {exc}",
            )
        except Exception as exc:
            return self._finish_degraded(
                incident,
                opening,
                evidence_gateway,
                started_clock,
                started_at,
                f"Opening evidence was unavailable: {exc}",
            )

        candidates = prefilter(incident, opening)
        confounded = bool(candidates.get("signature", {}).get("structurally_inseparable"))
        ledger = HypothesisLedger.from_prefilter(candidates, trail=evidence_gateway.trail)
        prompt = assemble_prompt(incident, opening, candidates)
        prompt += "\n\nHypothesis ledger (do not mark a candidate contradicted without a cited contradictory result):\n"
        prompt += json.dumps(ledger.context(), sort_keys=True, indent=2)
        assert_prompt_safe(prompt)
        conversation: list[Any] = [{"role": "user", "content": prompt}]

        try:
            client = self.client or OpenAI(**openai_client_kwargs())
            _response, conversation, _stopped_without_tools = self._gather(
                client,
                conversation,
                evidence_gateway,
                started_clock,
            )
            final_response = self._final_call(
                client, conversation, started_clock, validation_errors=None
            )
            payload = _payload_from_response(final_response)
            result, errors = self._validate(
                payload, incident, evidence_gateway, ledger, confounded=confounded
            )
            if result is not None:
                return self._finish(result, evidence_gateway.trail, started_clock, started_at)
            return self._retry_final(
                client,
                conversation,
                incident,
                evidence_gateway,
                ledger,
                started_clock,
                started_at,
                errors,
                confounded=confounded,
            )
        except InvestigationTimeout as exc:
            return self._finish_degraded(
                incident,
                opening,
                evidence_gateway,
                started_clock,
                started_at,
                f"The investigation deadline expired: {exc}",
            )
        except Exception as exc:
            return self._finish_degraded(
                incident,
                opening,
                evidence_gateway,
                started_clock,
                started_at,
                f"The investigation agent was unavailable: {exc}",
            )

    run = investigate
    investigate_incident = investigate

    def _gather(
        self,
        client: Any,
        conversation: list[Any],
        gateway: EvidenceGateway,
        started_clock: float,
    ) -> tuple[Any, list[Any], bool]:
        if self.max_turns == 0:
            return None, conversation, False
        last_response: Any = None
        for _turn in range(self.max_turns):
            last_response = self._model_call(
                client,
                conversation,
                started_clock,
                with_tools=True,
            )
            calls = _function_calls(last_response)
            if not calls:
                return last_response, conversation, True
            for call in calls:
                call_id = str(call.get("call_id") or f"call-{len(conversation)}")
                name = str(call.get("name", ""))
                arguments = call.get("arguments", {})
                if not isinstance(arguments, Mapping):
                    arguments = {}
                tool_response = self._bounded(
                    lambda name=name, arguments=dict(arguments): gateway.call(name, arguments),
                    self._remaining(started_clock),
                )
                conversation.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(arguments, sort_keys=True),
                    }
                )
                conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(tool_response, sort_keys=True, default=str),
                    }
                )
        return last_response, conversation, False

    def _final_call(
        self,
        client: Any,
        conversation: list[Any],
        started_clock: float,
        validation_errors: Sequence[str] | None,
    ) -> Any:
        final_input = list(conversation)
        instruction = "Return only the C4 investigation result JSON object now."
        if validation_errors:
            instruction += " Correct these validation errors from the previous object:\n- "
            instruction += "\n- ".join(validation_errors)
        final_input.append({"role": "user", "content": instruction})
        return self._model_call(
            client,
            final_input,
            started_clock,
            with_tools=False,
            structured=True,
        )

    def _retry_final(
        self,
        client: Any,
        conversation: list[Any],
        incident: Mapping[str, Any],
        gateway: EvidenceGateway,
        ledger: HypothesisLedger,
        started_clock: float,
        started_at: str,
        errors: Sequence[str],
        *,
        confounded: bool = False,
    ) -> InvestigationRun:
        try:
            response = self._final_call(client, conversation, started_clock, errors)
            payload = _payload_from_response(response)
            result, second_errors = self._validate(
                payload, incident, gateway, ledger, confounded=confounded
            )
            if result is not None:
                return self._finish(result, gateway.trail, started_clock, started_at)
            reason = "; ".join(second_errors or errors)
        except InvestigationTimeout as exc:
            reason = f"retry exceeded the investigation deadline: {exc}"
        except Exception as exc:
            reason = f"retry failed: {exc}"
        return self._finish_degraded(
            incident,
            {},
            gateway,
            started_clock,
            started_at,
            f"The result remained invalid after one retry: {reason}",
        )

    def _validate(
        self,
        payload: Mapping[str, Any] | None,
        incident: Mapping[str, Any],
        gateway: EvidenceGateway,
        ledger: HypothesisLedger,
        *,
        confounded: bool = False,
    ) -> tuple[InvestigationResult | None, list[str]]:
        if not isinstance(payload, Mapping):
            return None, ["model did not return a JSON object"]
        try:
            result = InvestigationResult.model_validate(payload)
        except ValidationError as exc:
            return None, [_validation_error(error) for error in exc.errors()]
        errors: list[str] = []
        if result.incident_id != str(incident.get("incident_id", "")):
            errors.append("incident_id does not match the investigated incident")
        if _contains_key(result.model_dump(mode="python"), "severity"):
            errors.append("severity is forbidden in C4 results")
        errors.extend(_citation_errors(result, gateway))
        if confounded and not result.competing_explanations:
            errors.append("a structurally confounded case must name a competing explanation")
        if confounded and result.diagnostic_confidence == "high":
            errors.append("a structurally confounded case cannot claim high diagnostic confidence")
        if result.outcome != "agent_unavailable":
            errors.extend(_claim_evidence_errors(result))
        if errors:
            return None, errors
        return result, []

    def _model_call(
        self,
        client: Any,
        conversation: list[Any],
        started_clock: float,
        *,
        with_tools: bool,
        structured: bool = False,
    ) -> Any:
        if self._remaining(started_clock) <= 0:
            raise InvestigationTimeout("wall-clock budget exhausted")
        kwargs: MutableMapping[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_PROMPT,
            "input": conversation,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort and self._reasoning_supported is not False:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if with_tools:
            kwargs["tools"] = TOOL_DEFINITIONS
            kwargs["parallel_tool_calls"] = False
        if structured:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "investigation_result",
                    "strict": True,
                    "schema": _strict_schema(InvestigationResult.model_json_schema()),
                }
            }
        responses = getattr(client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            raise TypeError("injected client must expose responses.create")
        try:
            return self._bounded(
                lambda: create(**kwargs),
                self._remaining(started_clock),
            )
        except Exception as exc:
            if "reasoning" not in kwargs or not _is_reasoning_unsupported_error(exc):
                raise
            self._reasoning_supported = False
            kwargs.pop("reasoning")
            return self._bounded(
                lambda: create(**kwargs),
                self._remaining(started_clock),
            )

    def _bounded(self, callback: Any, timeout: float) -> Any:
        if timeout <= 0:
            raise InvestigationTimeout("wall-clock budget exhausted")
        value: list[Any] = []
        error: list[BaseException] = []

        def invoke() -> None:
            try:
                value.append(callback())
            except BaseException as exc:  # propagate into the bounded caller
                error.append(exc)

        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise InvestigationTimeout("bounded call did not return")
        if error:
            raise error[0]
        return value[0] if value else None

    def _remaining(self, started_clock: float) -> float:
        return self.timeout_seconds - (time.monotonic() - started_clock)

    def _finish(
        self,
        result: InvestigationResult,
        trail: EvidenceTrail,
        started_clock: float,
        started_at: str,
    ) -> InvestigationRun:
        return InvestigationRun(
            result=result,
            trail=trail,
            started_at=started_at,
            completed_at=_utc_now(),
            duration_ms=round((time.monotonic() - started_clock) * 1000.0, 3),
        )

    def _finish_degraded(
        self,
        incident: Mapping[str, Any],
        opening: Mapping[str, Mapping[str, Any]],
        gateway: EvidenceGateway,
        started_clock: float,
        started_at: str,
        reason: str,
    ) -> InvestigationRun:
        result = degrade_result(
            incident, opening, gateway.trail, reason=redact_secrets(reason)
        )
        return self._finish(result, gateway.trail, started_clock, started_at)


def run_investigation(
    incident: Mapping[str, Any],
    *,
    client: Any | None = None,
    gateway: EvidenceGateway | None = None,
    **agent_options: Any,
) -> InvestigationRun:
    """Convenience entry point for one bounded investigation."""
    return InvestigationAgent(client, **agent_options).investigate(incident, gateway)


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic defaults to the Responses strict-schema requirement."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["required"] = list(properties)
        schema.pop("default", None)
    for value in schema.values():
        if isinstance(value, dict):
            _strict_schema(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strict_schema(item)
    return schema


def _reasoning_support(model: str) -> bool | None:
    """Use verified capability facts and probe models not yet classified."""
    if model == DEFAULT_MODEL:
        return True
    if model == "gpt-4.1-mini":
        return False
    return None


def _is_reasoning_unsupported_error(error: BaseException) -> bool:
    """Recognise an API rejection of the optional reasoning parameter."""
    message = str(error).lower()
    return "reasoning" in message and any(
        marker in message
        for marker in ("unsupported", "not supported", "unknown", "unrecognized", "invalid", "not allowed")
    )


def _function_calls(response: Any) -> list[dict[str, Any]]:
    output = _value(response, "output", [])
    if isinstance(response, Mapping) and not output:
        output = response.get("tool_calls", [])
    calls: list[dict[str, Any]] = []
    for item in output or []:
        item_type = _value(item, "type", "")
        if item_type not in {"function_call", "tool_call"}:
            continue
        arguments = _value(item, "arguments", _value(item, "input", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            {
                "call_id": _value(item, "call_id", _value(item, "id", "")),
                "name": _value(item, "name", ""),
                "arguments": arguments,
            }
        )
    return calls


def _payload_from_response(response: Any) -> Mapping[str, Any] | None:
    if response is None:
        return None
    parsed = _value(response, "output_parsed", None)
    if isinstance(parsed, InvestigationResult):
        return parsed.model_dump(mode="python")
    if isinstance(parsed, Mapping):
        return parsed
    if isinstance(response, Mapping) and "incident_id" in response:
        return response
    text = _value(response, "output_text", None)
    if not isinstance(text, str):
        output = _value(response, "output", [])
        pieces: list[str] = []
        for item in output or []:
            for content in _value(item, "content", []) or []:
                value = _value(content, "text", None)
                if isinstance(value, str):
                    pieces.append(value)
        text = "\n".join(pieces)
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _citation_errors(result: InvestigationResult, gateway: EvidenceGateway) -> list[str]:
    errors: list[str] = []
    for citation in _citations(result.model_dump(mode="python")):
        query_id = str(citation.get("query_id", ""))
        tool = str(citation.get("tool", ""))
        if not query_id:
            errors.append("every evidence item must contain query_id")
            continue
        entry = next(
            (entry for entry in gateway.trail.entries if entry.get("query_id") == query_id),
            None,
        )
        if entry is None or not gateway.verify_citation(query_id):
            errors.append(f"citation {query_id!r} does not identify an executed gateway query")
        elif entry.get("tool") != tool:
            errors.append(
                f"citation {query_id!r} names tool {tool!r}, but gateway recorded {entry.get('tool')!r}"
            )
    return errors


def _citations(value: Any):
    if isinstance(value, Mapping):
        if "query_id" in value or "tool" in value:
            yield value
        for nested in value.values():
            yield from _citations(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _citations(nested)


def _claim_evidence_errors(result: InvestigationResult) -> list[str]:
    errors: list[str] = []
    for index, fact in enumerate(result.confirmed_facts):
        if not fact.evidence:
            errors.append(f"confirmed_facts[{index}] has no evidence")
    if not result.leading_hypothesis.evidence:
        errors.append("leading_hypothesis has no evidence")
    for index, item in enumerate(result.competing_explanations):
        if not item.evidence:
            errors.append(f"competing_explanations[{index}] has no evidence")
    if not result.why_ambiguity_exists.evidence:
        errors.append("why_ambiguity_exists has no evidence")
    for index, item in enumerate(result.missing_evidence):
        if not item.evidence:
            errors.append(f"missing_evidence[{index}] has no evidence")
    for index, item in enumerate(result.supporting_evidence):
        if not item.query_id or not item.tool:
            errors.append(f"supporting_evidence[{index}] is not cited")
    if not result.recommended_next_action.basis:
        errors.append("recommended_next_action has no evidence basis")
    return errors


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return any(name == key or _contains_key(nested, key) for name, nested in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(nested, key) for nested in value)
    return False


def _validation_error(error: Mapping[str, Any]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "result"
    message = str(error.get("msg", "invalid value"))
    return f"{location}: {message}"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "InvestigationAgent",
    "InvestigationRun",
    "InvestigationTimeout",
    "SYSTEM_PROMPT",
    "run_investigation",
    "TOOL_DEFINITIONS",
]
