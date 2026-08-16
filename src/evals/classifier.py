"""Validate and score version 1 classifier results."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for

JsonDict = dict[str, Any]


class ClassifierError(RuntimeError):
    """Raised when a classifier result or hidden label is not valid."""


def _schema_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "schemas" / name


def _validate(document: Any, schema_name: str, label: str) -> JsonDict:
    schema = json.loads(_schema_path(schema_name).read_text(encoding="utf-8"))
    validator = validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ClassifierError(f"{label}: {path}: {first.message}")
    assert isinstance(document, dict)
    return document


def validate_classifier_result(document: Any) -> JsonDict:
    """Validate the result schema and ranking rules that JSON Schema cannot express."""
    result = _validate(document, "classifier-result.schema.json", "classifier result")
    if result["kind"] == "boolean" and not math.isfinite(float(result["confidence"])):
        raise ClassifierError("classifier result: confidence must be finite")
    if result["kind"] == "ranking":
        identifiers = [item["id"] for item in result["items"]]
        if len(identifiers) != len(set(identifiers)):
            raise ClassifierError("classifier result: ranking item ids must be unique")
        scores = [item["score"] for item in result["items"]]
        if any(
            not math.isfinite(float(item[field]))
            for item in result["items"]
            for field in ("score", "confidence")
        ):
            raise ClassifierError("classifier result: ranking scores and confidence must be finite")
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ClassifierError("classifier result: ranking scores must be in descending order")
    return result


def validate_classifier_expected(document: Any) -> JsonDict:
    """Validate a hidden expected label."""
    expected = _validate(document, "classifier-expected.schema.json", "classifier expected label")
    if expected["kind"] == "ranking" and any(
        not math.isfinite(float(grade)) for grade in expected.get("relevance_grades", {}).values()
    ):
        raise ClassifierError("classifier expected label: relevance grades must be finite")
    return expected


def _score(value: float, maximum: float = 1.0) -> JsonDict:
    return {"value": round(float(value), 12), "max": float(maximum)}


def _boolean_scores(result: JsonDict, expected: JsonDict) -> JsonDict:
    decision = bool(result["decision"])
    label = bool(expected["label"])
    positive_probability = (
        float(result["confidence"]) if decision else 1.0 - float(result["confidence"])
    )
    true_positive = int(decision and label)
    false_positive = int(decision and not label)
    false_negative = int(not decision and label)
    true_negative = int(not decision and not label)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / recall_denominator if recall_denominator else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    brier = (positive_probability - float(label)) ** 2
    return {
        "accuracy": _score(decision == label),
        "precision": _score(precision),
        "recall": _score(recall),
        "f1": _score(f1),
        "brier": _score(brier),
        "true_positive": _score(true_positive),
        "false_positive": _score(false_positive),
        "false_negative": _score(false_negative),
        "true_negative": _score(true_negative),
        "schema_validity": _score(1),
    }


def _discounted_gain(grades: list[float]) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def _validate_k(k: int) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ClassifierError("k must be a positive integer")
    return k


def _ranking_scores(result: JsonDict, expected: JsonDict, k: int) -> JsonDict:
    required = set(expected["required_ids"])
    ranked_ids = [item["id"] for item in result["items"]]
    top_ids = ranked_ids[:k]
    hits = [identifier for identifier in top_ids if identifier in required]
    recall = len(hits) / len(required) if required else 1.0

    first_rank = next(
        (index for index, identifier in enumerate(ranked_ids, start=1) if identifier in required),
        None,
    )
    reciprocal_rank = 1.0 / first_rank if first_rank is not None else 0.0
    relevance_grades = expected.get("relevance_grades", {})

    def grade(identifier: str) -> float:
        return float(relevance_grades.get(identifier, 1.0 if identifier in required else 0.0))

    dcg = _discounted_gain([grade(identifier) for identifier in top_ids])
    ideal_ids = required | set(relevance_grades)
    ideal_grades = sorted((grade(identifier) for identifier in ideal_ids), reverse=True)[:k]
    ideal_dcg = _discounted_gain(ideal_grades)
    ndcg = dcg / ideal_dcg if ideal_dcg else 1.0

    labels = expected.get("binary_labels")
    brier: float | None = None
    calibration = 0.0
    if labels is not None:
        labeled_items = [item for item in result["items"] if item["id"] in labels]
        if labeled_items:
            brier = sum(
                (float(item["confidence"]) - float(labels[item["id"]])) ** 2
                for item in labeled_items
            ) / len(labeled_items)
            calibration = 1.0 - brier
    soft = 0.50 * recall + 0.30 * ndcg + 0.20 * calibration

    scores = {
        f"recall_at_{k}": _score(recall),
        "reciprocal_rank": _score(reciprocal_rank),
        f"ndcg_at_{k}": _score(ndcg),
        "soft_score": _score(soft),
        "required_top_k": _score(required.issubset(top_ids)),
        "schema_validity": _score(1),
    }
    if brier is not None:
        scores["brier"] = _score(brier)
    return scores


def score_classifier_result(result: Any, expected: Any, *, k: int = 20) -> JsonDict:
    """Score one classifier result against one hidden expected label."""
    valid_result = validate_classifier_result(result)
    valid_expected = validate_classifier_expected(expected)
    if valid_result["kind"] != valid_expected["kind"]:
        raise ClassifierError(
            f"classifier kind mismatch: result is {valid_result['kind']}, "
            f"expected label is {valid_expected['kind']}"
        )

    kind = valid_result["kind"]
    if kind == "boolean":
        passed = valid_result["decision"] == valid_expected["label"]
        scores = _boolean_scores(valid_result, valid_expected)
    else:
        k = _validate_k(k)
        top_ids = {item["id"] for item in valid_result["items"][:k]}
        passed = set(valid_expected["required_ids"]).issubset(top_ids)
        scores = _ranking_scores(valid_result, valid_expected, k)
    return {
        "schema_version": 1,
        "kind": kind,
        "pass": passed,
        "k": k if kind == "ranking" else None,
        "scores": scores,
    }


def load_and_score(result_path: Path, expected_path: Path, *, k: int = 20) -> JsonDict:
    """Load and score one result and one hidden label file."""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassifierError(f"cannot read classifier result {result_path}: {error}") from error
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassifierError(f"cannot read classifier expected label {expected_path}: {error}") from error
    return score_classifier_result(result, expected, k=k)
