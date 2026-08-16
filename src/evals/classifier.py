"""Validate and score version 1 classifier results."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from .pi import compile_schema, first_schema_error
from .resources import read_schema

JsonDict = dict[str, Any]

SCORE_PRECISION = 12

# nDCG gain is exponential in the grade, so an uncapped grade overflows float64
# (2**1024 is inf) and takes nDCG with it. 1000 is the shared AutoImprove cap,
# and classifier-expected.schema.json states the same maximum.
MAX_RELEVANCE_GRADE = 1000


class ClassifierError(RuntimeError):
    """Raised when a classifier result or hidden label is not valid."""


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Any:
    """Compile one contract schema. Raises HarnessError if it cannot be read."""
    return compile_schema(read_schema(schema_name))


def _validate(document: Any, schema_name: str, label: str) -> JsonDict:
    detail = first_schema_error(_validator(schema_name), document)
    if detail is not None:
        raise ClassifierError(f"{label}: {detail}")
    assert isinstance(document, dict)
    return document


def validate_classifier_result(document: Any) -> JsonDict:
    """Validate the result schema and ranking rules that JSON Schema cannot express."""
    result = _validate(document, "classifier-result.schema.json", "classifier result")
    if result["kind"] == "boolean" and not math.isfinite(float(result["confidence"])):
        raise ClassifierError("classifier result: confidence must be finite")
    if result["kind"] == "ranking":
        identifiers: list[str] = []
        scores: list[float] = []
        finite = True
        for item in result["items"]:
            identifiers.append(item["id"])
            scores.append(item["score"])
            finite = finite and all(
                math.isfinite(float(item[field])) for field in ("score", "confidence")
            )
        if len(identifiers) != len(set(identifiers)):
            raise ClassifierError("classifier result: ranking item ids must be unique")
        if not finite:
            raise ClassifierError("classifier result: ranking scores and confidence must be finite")
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ClassifierError("classifier result: ranking scores must be in descending order")
    return result


def validate_classifier_expected(document: Any) -> JsonDict:
    """Validate a hidden expected label."""
    # Check the grade range before the schema pass, for two reasons: the oneOf
    # error the schema emits names neither the field nor the constraint, and NaN
    # slips past minimum and maximum entirely (every comparison with NaN is
    # false). Anything else malformed still falls through to the schema error.
    if isinstance(document, dict) and document.get("kind") == "ranking":
        grades = document.get("relevance_grades")
        if isinstance(grades, dict) and any(
            isinstance(grade, (int, float))
            and not isinstance(grade, bool)
            and not (
                math.isfinite(float(grade))
                and 0 <= float(grade) <= MAX_RELEVANCE_GRADE
            )
            for grade in grades.values()
        ):
            raise ClassifierError(
                "classifier expected label: relevance grades must be finite and from 0 through "
                f"{MAX_RELEVANCE_GRADE}"
            )
    return _validate(document, "classifier-expected.schema.json", "classifier expected label")


def round_score(value: float) -> float:
    """Round to the one precision shared by per-cell and aggregate metrics."""
    return round(float(value), SCORE_PRECISION)


def score_entry(value: float) -> JsonDict:
    return {"value": round_score(value), "max": 1.0}


def precision_recall_f1(
    true_positive: float, false_positive: float, false_negative: float
) -> tuple[float, float, float]:
    """Return precision, recall, and F1 for one confusion-matrix triple."""
    predicted = true_positive + false_positive
    actual = true_positive + false_negative
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / actual if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def invalid_summary(error: str) -> JsonDict:
    """Return the scored-summary shape for a result that failed the contract."""
    return {
        "schema_version": 1,
        "kind": "invalid",
        "pass": False,
        "k": None,
        "scores": {"schema_validity": score_entry(0)},
        "error": error,
    }


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
    precision, recall, f1 = precision_recall_f1(true_positive, false_positive, false_negative)
    brier = (positive_probability - float(label)) ** 2
    return {
        "accuracy": score_entry(decision == label),
        "precision": score_entry(precision),
        "recall": score_entry(recall),
        "f1": score_entry(f1),
        "brier": score_entry(brier),
        "true_positive": score_entry(true_positive),
        "false_positive": score_entry(false_positive),
        "false_negative": score_entry(false_negative),
        "true_negative": score_entry(true_negative),
        "schema_validity": score_entry(1),
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
    top_set = set(top_ids)
    recall = len(required & top_set) / len(required) if required else 1.0

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
        f"recall_at_{k}": score_entry(recall),
        "reciprocal_rank": score_entry(reciprocal_rank),
        f"ndcg_at_{k}": score_entry(ndcg),
        "soft_score": score_entry(soft),
        "required_top_k": score_entry(required.issubset(top_set)),
        "schema_validity": score_entry(1),
    }
    if brier is not None:
        scores["brier"] = score_entry(brier)
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
        scores = _boolean_scores(valid_result, valid_expected)
        passed = bool(scores["accuracy"]["value"])
    else:
        k = _validate_k(k)
        scores = _ranking_scores(valid_result, valid_expected, k)
        passed = bool(scores["required_top_k"]["value"])
    return {
        "schema_version": 1,
        "kind": kind,
        "pass": passed,
        "k": k if kind == "ranking" else None,
        "scores": scores,
    }


def load_and_score(result_path: Path, expected_path: Path, *, k: int = 20) -> JsonDict:
    """Load and score one result and one hidden label file."""
    return score_classifier_result(
        _read_json(result_path, "classifier result"),
        _read_json(expected_path, "classifier expected label"),
        k=k,
    )


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassifierError(f"cannot read {label} {path}: {error}") from error
