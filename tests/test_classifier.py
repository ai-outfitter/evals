"""Tests for the version 1 classifier contract and deterministic scorer."""

from __future__ import annotations

import json
import math
import pathlib
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.classifier import (
    ClassifierError,
    score_classifier_result,
    validate_classifier_expected,
    validate_classifier_result,
)
from evals.cli import main
from evals.reduce import classifier_metrics
from evals.spec import run_paths
from evals.bundle import clean_run

REPO = pathlib.Path(__file__).resolve().parents[1]


def boolean_result(decision: bool = True, confidence: float = 0.8) -> dict:
    return {
        "schema_version": 1,
        "kind": "boolean",
        "decision": decision,
        "confidence": confidence,
        "evidence_refs": ["source:1"],
    }


def ranking_result() -> dict:
    return {
        "schema_version": 1,
        "kind": "ranking",
        "items": [
            {"id": "a", "score": 0.9, "confidence": 0.8, "evidence_refs": ["source:a"]},
            {"id": "b", "score": 0.7, "confidence": 0.7, "evidence_refs": ["source:b"]},
            {"id": "c", "score": 0.4, "confidence": 0.6, "evidence_refs": ["source:c"]},
            {"id": "d", "score": 0.2, "confidence": 0.6, "evidence_refs": ["source:d"]},
            {"id": "e", "score": 0.1, "confidence": 0.5, "evidence_refs": ["source:e"]},
        ],
    }


class ClassifierSchemaTests(unittest.TestCase):
    def test_result_schema_identity_matches_the_canonical_contract(self):
        schema = json.loads(
            (REPO / "schemas" / "classifier-result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["$id"],
            "https://ai-outfitter.dev/schemas/classifier-result-v1.schema.json",
        )
        self.assertEqual(schema["title"], "ClassifierResultV1")
        self.assertEqual(len(schema["oneOf"]), 2)
        self.assertTrue(all(mode["additionalProperties"] is False for mode in schema["oneOf"]))

    def test_boolean_contract_rejects_unknown_properties(self):
        result = boolean_result()
        result["reasoning"] = "not part of the contract"
        with self.assertRaises(ClassifierError):
            validate_classifier_result(result)

    def test_ranking_contract_rejects_duplicate_ids(self):
        result = ranking_result()
        result["items"][1]["id"] = "a"
        with self.assertRaisesRegex(ClassifierError, "ids must be unique"):
            validate_classifier_result(result)

    def test_ranking_contract_rejects_ascending_score_step(self):
        result = ranking_result()
        result["items"][2]["score"] = 0.8
        with self.assertRaisesRegex(ClassifierError, "descending order"):
            validate_classifier_result(result)

    def test_expected_contract_rejects_negative_relevance_grade(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
            "relevance_grades": {"a": -1},
        }
        with self.assertRaises(ClassifierError):
            validate_classifier_expected(expected)


class ClassifierFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (REPO / "tests" / "fixtures" / "classifier" / "cases.json").read_text(
                encoding="utf-8"
            )
        )

    def test_fixture_count_balance_and_fixed_split(self):
        cases = self.fixture["cases"]
        boolean = [case for case in cases if case["expected"]["kind"] == "boolean"]
        ranking = [case for case in cases if case["expected"]["kind"] == "ranking"]
        self.assertGreaterEqual(len(boolean), 40)
        self.assertGreaterEqual(len(ranking), 20)
        self.assertEqual(sum(case["expected"]["label"] for case in boolean), len(boolean) / 2)
        self.assertEqual(
            {split: sum(case["split"] == split for case in boolean) for split in ("train", "validation", "test")},
            self.fixture["assignment"]["boolean"],
        )
        self.assertEqual(
            {split: sum(case["split"] == split for case in ranking) for split in ("train", "validation", "test")},
            self.fixture["assignment"]["ranking"],
        )

    def test_every_static_case_is_valid_and_scoreable(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                score_classifier_result(case["result"], case["expected"], k=5)
                if case["expected"]["kind"] == "ranking":
                    self.assertGreaterEqual(len(case["result"]["items"]), 5)

    def test_fixture_does_not_claim_a_model_run_or_human_review(self):
        self.assertIn("do not record a model run", self.fixture["claim_boundary"].lower())
        self.assertEqual(self.fixture["human_review"]["status"], "pending")


class ClassifierScoringTests(unittest.TestCase):
    def test_boolean_probability_and_brier_use_decision_confidence(self):
        result = score_classifier_result(
            boolean_result(decision=False, confidence=0.8),
            {"schema_version": 1, "kind": "boolean", "label": True},
        )
        self.assertFalse(result["pass"])
        self.assertAlmostEqual(result["scores"]["brier"]["value"], 0.64)
        self.assertEqual(result["scores"]["false_negative"]["value"], 1)

    def test_ranking_metrics_match_the_canonical_scorer(self):
        result_value = {
            "schema_version": 1,
            "kind": "ranking",
            "items": [
                {"id": "a", "score": 0.9, "confidence": 0.8, "evidence_refs": ["source-a"]},
                {"id": "x", "score": 0.8, "confidence": 0.4, "evidence_refs": ["source-x"]},
                {"id": "b", "score": 0.7, "confidence": 0.6, "evidence_refs": ["source-b"]},
            ],
        }
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a", "b"],
            "relevance_grades": {"a": 3, "b": 2},
            "binary_labels": {"a": True, "x": False},
        }
        result = score_classifier_result(result_value, expected, k=2)
        self.assertFalse(result["pass"])
        self.assertEqual(result["scores"]["recall_at_2"]["value"], 0.5)
        self.assertEqual(result["scores"]["reciprocal_rank"]["value"], 1.0)
        expected_ndcg = 7 / (7 + 3 / math.log2(3))
        self.assertAlmostEqual(result["scores"]["ndcg_at_2"]["value"], expected_ndcg)
        self.assertAlmostEqual(result["scores"]["brier"]["value"], 0.1)
        expected_soft = (
            0.50 * result["scores"]["recall_at_2"]["value"]
            + 0.30 * result["scores"]["ndcg_at_2"]["value"]
            + 0.20 * 0.9
        )
        self.assertAlmostEqual(result["scores"]["soft_score"]["value"], expected_soft)

    def test_ranking_soft_score_uses_zero_calibration_without_labels(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
        }
        result = score_classifier_result(ranking_result(), expected)
        self.assertNotIn("brier", result["scores"])
        self.assertAlmostEqual(result["scores"]["soft_score"]["value"], 0.8)
        self.assertEqual(result["k"], 20)

    def test_ranking_brier_uses_all_returned_labeled_items(self):
        result_value = {
            "schema_version": 1,
            "kind": "ranking",
            "items": [
                {"id": "a", "score": 1.0, "confidence": 1.0, "evidence_refs": []},
                {"id": "b", "score": 0.5, "confidence": 0.0, "evidence_refs": []},
            ],
        }
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
            "binary_labels": {"a": True, "b": True},
        }
        result = score_classifier_result(result_value, expected, k=1)
        self.assertEqual(result["scores"]["brier"]["value"], 0.5)
        self.assertEqual(result["scores"]["soft_score"]["value"], 0.9)

    def test_hard_pass_requires_every_required_id_in_top_k(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a", "c"],
        }
        self.assertFalse(score_classifier_result(ranking_result(), expected, k=2)["pass"])
        self.assertTrue(score_classifier_result(ranking_result(), expected, k=3)["pass"])

    def test_ranking_cutoff_must_be_a_positive_integer(self):
        expected = {"schema_version": 1, "kind": "ranking", "required_ids": []}
        for invalid in (0, -1, 1.5, True):
            with self.subTest(k=invalid), self.assertRaisesRegex(
                ClassifierError, "positive integer"
            ):
                score_classifier_result(ranking_result(), expected, k=invalid)

    def test_cli_scores_separate_hidden_label_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result_path = root / "result.json"
            expected_path = root / "expected.json"
            result_path.write_text(json.dumps(boolean_result()), encoding="utf-8")
            expected_path.write_text(
                json.dumps({"schema_version": 1, "kind": "boolean", "label": True}),
                encoding="utf-8",
            )
            self.assertEqual(main(["score-classifier", str(result_path), str(expected_path)]), 0)


class ClassifierReportTests(unittest.TestCase):
    def test_boolean_report_metrics_use_confusion_totals(self):
        def cell(scores: dict) -> dict:
            return {
                "experiment": {"kind": "transform-classifier"},
                "outcome": {"scores": scores},
            }

        true_positive = score_classifier_result(
            boolean_result(True, 0.8),
            {"schema_version": 1, "kind": "boolean", "label": True},
        )["scores"]
        false_positive = score_classifier_result(
            boolean_result(True, 0.7),
            {"schema_version": 1, "kind": "boolean", "label": False},
        )["scores"]
        summary = classifier_metrics({"a": cell(true_positive), "b": cell(false_positive)})
        self.assertEqual(summary["schema_validity_rate"], 1.0)
        self.assertEqual(summary["boolean"]["accuracy"], 0.5)
        self.assertEqual(summary["boolean"]["precision"], 0.5)
        self.assertEqual(summary["boolean"]["recall"], 1.0)

    def test_classifier_stub_pipeline_reports_invalid_schema(self):
        run_id = f"classifier-{uuid.uuid4().hex[:8]}"
        experiment = REPO / "experiments" / "exp-003-classifier-contract" / "experiment.yaml"
        paths = run_paths(REPO, "exp-003-classifier-contract", run_id)
        try:
            self.assertEqual(
                main(
                    [
                        "run",
                        str(experiment),
                        "--run-id",
                        run_id,
                        "--stub",
                        "--filter",
                        "agent=classifier-seed",
                    ]
                ),
                0,
            )
            result_paths = sorted(paths.cells.glob("*/result.json"))
            self.assertEqual(len(result_paths), 3)
            result = json.loads(result_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(result["outcome"]["status"], "fail")
            self.assertEqual(result["outcome"]["failure_class"], "benchmark")
            self.assertEqual(result["outcome"]["scores"]["schema_validity"]["value"], 0)
            self.assertEqual(main(["reduce", str(experiment), "--run-id", run_id]), 0)
            summary = json.loads((paths.root / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["classifier_metrics"]["schema_validity_rate"], 0)
        finally:
            clean_run(paths)


if __name__ == "__main__":
    unittest.main()
