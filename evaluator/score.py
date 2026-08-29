#!/usr/bin/env python3
"""Score a completed diagnosis against an after-the-fact hidden-truth record."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


DIMENSIONS = (
    "merchant_id",
    "provider",
    "payment_method",
    "card_network",
    "country",
    "issuing_bank",
)
_DIMENSION_SET = frozenset(DIMENSIONS)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _scenario_id(hidden_truth: Mapping[str, Any]) -> str:
    for source in (hidden_truth, _as_mapping(hidden_truth.get("injected"))):
        if source is not None and isinstance(source.get("scenario_id"), str):
            return source["scenario_id"]
    return "unknown"


def _cohort_from_value(value: Any) -> dict[str, Any] | None:
    mapping = _as_mapping(value)
    if mapping is None:
        return None
    if "cohort" in mapping and _as_mapping(mapping["cohort"]) is not None:
        return dict(mapping["cohort"])
    if any(key in _DIMENSION_SET for key in mapping):
        return dict(mapping)
    if not mapping:
        return {}
    return None


def _diagnosed_cohort(diagnosis: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    result = _as_mapping(diagnosis.get("investigation_result"))
    if result is None:
        result = _as_mapping(diagnosis.get("result"))
    containers = [diagnosis]
    if result is not None:
        containers.append(result)
    for container in containers:
        for key in ("diagnosed_cohort", "diagnosed_localisation", "diagnosed_localization"):
            if key in container:
                cohort = _cohort_from_value(container[key])
                if cohort is not None:
                    return cohort, True
        for key in ("localisation", "localization", "cohort", "affected_cohort"):
            if key in container:
                cohort = _cohort_from_value(container[key])
                if cohort is not None:
                    return cohort, True
        for key in ("incident", "incident_record"):
            incident = _as_mapping(container.get(key))
            if incident is not None and "affected_cohort" in incident:
                cohort = _cohort_from_value(incident["affected_cohort"])
                if cohort is not None:
                    return cohort, True
    return {}, False


def _injected_cohort(hidden_truth: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    injected = _as_mapping(hidden_truth.get("injected"))
    sources = [injected, _as_mapping(hidden_truth.get("injected_incident")), hidden_truth]
    for source in sources:
        if source is not None and "affected_cohort" in source:
            cohort = _cohort_from_value(source["affected_cohort"])
            if cohort is not None:
                return cohort, True
    return {}, False


def _pair_key(dimension: str, value: Any) -> tuple[str, str]:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        encoded = repr(value)
    return dimension, encoded


def _pair_list(cohort: Mapping[str, Any], keys: Iterable[str]) -> list[dict[str, Any]]:
    return [{"dimension": key, "value": cohort[key]} for key in sorted(keys)]


def _cohort_score(
    diagnosed: Mapping[str, Any],
    expected: Mapping[str, Any],
    diagnosed_present: bool,
    expected_present: bool,
) -> dict[str, Any]:
    diagnosed_pairs = {_pair_key(key, value) for key, value in diagnosed.items()}
    expected_pairs = {_pair_key(key, value) for key, value in expected.items()}
    matched_pairs = diagnosed_pairs & expected_pairs
    missing_pairs = expected_pairs - diagnosed_pairs
    spurious_pairs = diagnosed_pairs - expected_pairs

    if diagnosed_pairs:
        precision = len(matched_pairs) / len(diagnosed_pairs)
    else:
        precision = 1.0 if not expected_pairs else 0.0
    if expected_pairs:
        recall = len(matched_pairs) / len(expected_pairs)
    else:
        recall = 1.0 if not diagnosed_pairs else 0.0

    matched_dimensions = sorted(
        key for key, value in expected.items() if key in diagnosed and diagnosed[key] == value
    )
    missing_dimensions = sorted(
        key for key, value in expected.items() if _pair_key(key, value) in missing_pairs
    )
    spurious_dimensions = sorted(
        key for key, value in diagnosed.items() if _pair_key(key, value) in spurious_pairs
    )
    mismatched_dimensions = [
        {"dimension": key, "expected": expected[key], "diagnosed": diagnosed[key]}
        for key in sorted(set(expected) & set(diagnosed))
        if expected[key] != diagnosed[key]
    ]

    passed = diagnosed_present and expected_present and precision == 1.0 and recall == 1.0
    return {
        "passed": passed,
        "precision": precision,
        "recall": recall,
        "matched_dimensions": matched_dimensions,
        "missing_dimensions": missing_dimensions,
        "spurious_dimensions": spurious_dimensions,
        "mismatched_dimensions": mismatched_dimensions,
        "matched_pairs": _pair_list(expected, matched_dimensions),
        "missing_pairs": _pair_list(expected, missing_dimensions),
        "spurious_pairs": _pair_list(diagnosed, spurious_dimensions),
        "diagnosed_cohort_present": diagnosed_present,
        "injected_cohort_present": expected_present,
    }


def _result_value(diagnosis: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if key in diagnosis:
        return diagnosis[key]
    for nested_key in ("investigation_result", "result"):
        nested = _as_mapping(diagnosis.get(nested_key))
        if nested is not None and key in nested:
            return nested[key]
    return default


def _has_text(value: Any, fields: Sequence[str]) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    mapping = _as_mapping(value)
    if mapping is None:
        return False
    return any(isinstance(mapping.get(field), str) and mapping[field].strip() for field in fields)


def _named_competitors(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if _has_text(item, ("explanation", "statement", "name", "cause"))
    ]


def _missing_requests(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if _has_text(item, ("request", "reason", "statement", "evidence"))
    ]


def _truth_evaluation(hidden_truth: Mapping[str, Any]) -> Mapping[str, Any]:
    value = hidden_truth.get("evaluation")
    return _as_mapping(value) or {}


def _priority_relations(hidden_truth: Mapping[str, Any]) -> list[dict[str, str]]:
    evaluation = _truth_evaluation(hidden_truth)
    raw = evaluation.get("priority_relations", [])
    if not isinstance(raw, list):
        return []
    relations: list[dict[str, str]] = []
    for item in raw:
        mapping = _as_mapping(item)
        if mapping is None or not isinstance(mapping.get("scenario_id"), str):
            continue
        relation = mapping.get("relation", "outranks")
        if isinstance(relation, str):
            relations.append({"relation": relation, "scenario_id": mapping["scenario_id"]})
    return relations


def _uncertainty_score(diagnosis: Mapping[str, Any], hidden_truth: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = _truth_evaluation(hidden_truth)
    confounded = bool(evaluation.get("confounded", False))
    if not confounded:
        return {
            "applicable": False,
            "passed": True,
            "score": 1.0,
            "status": "not_applicable",
            "reason": "The hidden scenario is not marked observationally confounded.",
        }

    leading = _result_value(diagnosis, "leading_hypothesis")
    competing = _named_competitors(_result_value(diagnosis, "competing_explanations", []))
    missing = _missing_requests(_result_value(diagnosis, "missing_evidence", []))
    confidence = _result_value(diagnosis, "diagnostic_confidence")
    requirements = {
        "leading_hypothesis": _has_text(leading, ("statement", "explanation", "cause")),
        "named_competing_explanation": bool(competing),
        "missing_discriminating_evidence": bool(missing),
        "confidence_is_bounded": confidence in ("low", "medium"),
    }
    failures = [name for name, satisfied in requirements.items() if not satisfied]
    return {
        "applicable": True,
        "passed": not failures,
        "score": 1.0 if not failures else 0.0,
        "status": "pass" if not failures else "fail",
        "requirements": requirements,
        "failure_reasons": failures,
        "diagnostic_confidence": confidence,
        "reason": (
            "A confounded result is correctly hedged."
            if not failures
            else "A confounded result must not assert a confident single cause."
        ),
    }


def _priority_rank(diagnosis: Mapping[str, Any]) -> int | float | None:
    containers: list[Mapping[str, Any]] = [diagnosis]
    for key in ("incident", "incident_record", "detector"):
        nested = _as_mapping(diagnosis.get(key))
        if nested is not None:
            containers.append(nested)
    for container in containers:
        for key in ("priority_rank", "business_priority_rank"):
            value = container.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
    return None


def _pending_priority_score(relations: list[dict[str, str]]) -> dict[str, Any]:
    if not relations:
        return {
            "applicable": False,
            "passed": True,
            "score": 1.0,
            "status": "not_applicable",
            "checks": [],
            "reason": "The scenario declares no relative priority relation.",
        }
    return {
        "applicable": True,
        "passed": False,
        "score": 0.0,
        "status": "not_evaluable",
        "checks": [],
        "required_relations": relations,
        "reason": "A peer diagnosis is required to check the declared ordering.",
    }


def _overall_verdict(components: Mapping[str, Mapping[str, Any]]) -> tuple[str, bool]:
    passed = all(component.get("passed") is True for component in components.values())
    return ("pass" if passed else "fail"), passed


def score_diagnosis(
    diagnosis: Mapping[str, Any], hidden_truth: Mapping[str, Any]
) -> dict[str, Any]:
    """Return component scores for one diagnosis and one quarantined truth record.

    The optional evaluator envelope may carry ``diagnosed_cohort`` and ``priority_rank``
    alongside a C4 investigation result. A C4 result can also be passed directly when its
    surrounding incident carries ``affected_cohort``.
    """
    if not isinstance(diagnosis, Mapping):
        raise TypeError("diagnosis must be a JSON object")
    if not isinstance(hidden_truth, Mapping):
        raise TypeError("hidden_truth must be a JSON object")

    diagnosed, diagnosed_present = _diagnosed_cohort(diagnosis)
    expected, expected_present = _injected_cohort(hidden_truth)
    components = {
        "cohort_localisation": _cohort_score(
            diagnosed, expected, diagnosed_present, expected_present
        ),
        "uncertainty_handling": _uncertainty_score(diagnosis, hidden_truth),
        "severity_ordering": _pending_priority_score(_priority_relations(hidden_truth)),
    }
    verdict, passed = _overall_verdict(components)
    return {
        "scenario_id": _scenario_id(hidden_truth),
        "verdict": verdict,
        "passed": passed,
        "components": components,
        "diagnosed_cohort": diagnosed,
        "injected_cohort": expected,
    }


def _ordering_check(
    source_score: dict[str, Any],
    relation: Mapping[str, str],
    source_diagnosis: Mapping[str, Any],
    target_diagnosis: Mapping[str, Any],
) -> dict[str, Any]:
    source_id = source_score["scenario_id"]
    target_id = relation["scenario_id"]
    source_rank = _priority_rank(source_diagnosis)
    target_rank = _priority_rank(target_diagnosis)
    relation_name = relation["relation"]
    if relation_name in ("outranks", "above", "higher"):
        passed = source_rank is not None and target_rank is not None and source_rank < target_rank
        expectation = f"{source_id} ranks above {target_id}"
    elif relation_name in ("below", "ranks_below", "lower"):
        passed = source_rank is not None and target_rank is not None and source_rank > target_rank
        expectation = f"{source_id} ranks below {target_id}"
    else:
        passed = False
        expectation = f"unsupported relative-priority relation {relation_name!r}"
    return {
        "source_scenario_id": source_id,
        "target_scenario_id": target_id,
        "relation": relation_name,
        "expected": expectation,
        "source_priority_rank": source_rank,
        "target_priority_rank": target_rank,
        "passed": passed,
        "status": "pass" if passed else "fail",
        "uses_absolute_severity": False,
    }


def score_rankings(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Score cases and evaluate their declared relative-priority relations.

    Each case is ``{"diagnosis": <object>, "hidden_truth": <object>}``. Ordering compares
    supplied ranks only: a lower rank is higher business priority. It deliberately does not
    compare or require absolute severity labels.
    """
    entries = list(cases)
    scores: list[dict[str, Any]] = []
    by_id: dict[str, tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]] = {}
    for entry in entries:
        diagnosis = entry.get("diagnosis")
        hidden_truth = entry.get("hidden_truth")
        if not isinstance(diagnosis, Mapping) or not isinstance(hidden_truth, Mapping):
            raise TypeError("each ranking case needs diagnosis and hidden_truth JSON objects")
        score = score_diagnosis(diagnosis, hidden_truth)
        scenario_id = score["scenario_id"]
        if scenario_id in by_id:
            raise ValueError(f"duplicate scenario_id in ranking: {scenario_id}")
        scores.append(score)
        by_id[scenario_id] = (score, diagnosis, hidden_truth)

    checks: list[dict[str, Any]] = []
    for score, diagnosis, hidden_truth in by_id.values():
        component = score["components"]["severity_ordering"]
        for relation in _priority_relations(hidden_truth):
            target = by_id.get(relation["scenario_id"])
            if target is None:
                check = {
                    "source_scenario_id": score["scenario_id"],
                    "target_scenario_id": relation["scenario_id"],
                    "relation": relation["relation"],
                    "expected": "peer diagnosis must be present",
                    "source_priority_rank": _priority_rank(diagnosis),
                    "target_priority_rank": None,
                    "passed": False,
                    "status": "not_evaluable",
                    "uses_absolute_severity": False,
                }
            else:
                check = _ordering_check(score, relation, diagnosis, target[1])
            checks.append(check)
        if component["applicable"]:
            own_checks = [
                check for check in checks if check["source_scenario_id"] == score["scenario_id"]
            ]
            component["checks"] = own_checks
            component["passed"] = bool(own_checks) and all(
                check["passed"] is True for check in own_checks
            )
            component["score"] = 1.0 if component["passed"] else 0.0
            component["status"] = "pass" if component["passed"] else "fail"
            component["reason"] = (
                "All declared relative-priority checks passed."
                if component["passed"]
                else "A declared relative-priority check failed or was not evaluable."
            )
        score["verdict"], score["passed"] = _overall_verdict(score["components"])

    passed = all(score["passed"] for score in scores) and all(
        check["passed"] is True for check in checks
    )
    return {
        "verdict": "pass" if passed else "fail",
        "passed": passed,
        "scores": scores,
        "ordering_checks": checks,
    }


def _load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        print("usage: python3 evaluator/score.py DIAGNOSIS.json HIDDEN_TRUTH.json", file=sys.stderr)
        return 2
    try:
        diagnosis = _load_json(args[0])
        hidden_truth = _load_json(args[1])
        result = score_diagnosis(diagnosis, hidden_truth)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"evaluator: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
