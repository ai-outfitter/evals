"""Tests for the version 1 classifier contract and deterministic scorer."""

from __future__ import annotations

import json
import math
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.classifier import (
    MAX_RELEVANCE_GRADE,
    ClassifierError,
    invalid_summary,
    score_classifier_result,
    validate_classifier_expected,
    validate_classifier_result,
)
from evals.cli import main
from evals.resources import read_schema
from evals.reduce import classifier_by_agent_lines, classifier_metrics, markdown_report

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

    def test_boolean_contract_rejects_a_not_a_number_confidence(self):
        """`json.loads` accepts the NaN literal, and NaN slips past minimum and
        maximum (both are comparisons, and every comparison with NaN is false),
        so only the finite guard stands between NaN and a NaN Brier score."""
        result = json.loads(
            '{"schema_version": 1, "kind": "boolean", "decision": true,'
            ' "confidence": NaN, "evidence_refs": ["source:1"]}'
        )
        self.assertTrue(math.isnan(result["confidence"]))
        with self.assertRaisesRegex(ClassifierError, "confidence must be finite"):
            validate_classifier_result(result)

    def test_ranking_contract_rejects_a_not_a_number_score(self):
        result = json.loads(
            '{"schema_version": 1, "kind": "ranking", "items": ['
            '{"id": "a", "score": NaN, "confidence": 0.8, "evidence_refs": ["source:a"]}]}'
        )
        self.assertTrue(math.isnan(result["items"][0]["score"]))
        # NaN also defeats the descending-order comparison, so the finite guard
        # has to be the one that fires.
        with self.assertRaisesRegex(ClassifierError, "must be finite"):
            validate_classifier_result(result)

    def test_ranking_contract_rejects_a_not_a_number_confidence(self):
        result = json.loads(
            '{"schema_version": 1, "kind": "ranking", "items": ['
            '{"id": "a", "score": 0.9, "confidence": NaN, "evidence_refs": ["source:a"]}]}'
        )
        self.assertTrue(math.isnan(result["items"][0]["confidence"]))
        with self.assertRaisesRegex(ClassifierError, "must be finite"):
            validate_classifier_result(result)

    def test_expected_contract_rejects_negative_relevance_grade(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
            "relevance_grades": {"a": -1},
        }
        with self.assertRaisesRegex(ClassifierError, "finite and from 0 through 1000"):
            validate_classifier_expected(expected)

    def test_expected_contract_accepts_the_maximum_relevance_grade(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
            "relevance_grades": {"a": MAX_RELEVANCE_GRADE},
        }
        self.assertEqual(
            validate_classifier_expected(expected)["relevance_grades"]["a"],
            MAX_RELEVANCE_GRADE,
        )

    def test_expected_contract_rejects_a_relevance_grade_above_the_maximum(self):
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a"],
            "relevance_grades": {"a": MAX_RELEVANCE_GRADE + 1},
        }
        with self.assertRaisesRegex(ClassifierError, "finite and from 0 through 1000"):
            validate_classifier_expected(expected)

    def test_expected_contract_rejects_an_infinite_relevance_grade(self):
        """`json.loads` accepts the Infinity literal, and an infinite grade makes
        the exponential gain infinite, so the range guard has to reject it."""
        expected = json.loads(
            '{"schema_version": 1, "kind": "ranking", "required_ids": ["a"],'
            ' "relevance_grades": {"a": Infinity}}'
        )
        self.assertTrue(math.isinf(expected["relevance_grades"]["a"]))
        with self.assertRaisesRegex(ClassifierError, "finite and from 0 through 1000"):
            validate_classifier_expected(expected)

    def test_expected_contract_rejects_a_not_a_number_relevance_grade(self):
        """NaN defeats both minimum and maximum (every comparison with NaN is
        false), so only the range guard stands between it and a NaN nDCG."""
        expected = json.loads(
            '{"schema_version": 1, "kind": "ranking", "required_ids": ["a"],'
            ' "relevance_grades": {"a": NaN}}'
        )
        self.assertTrue(math.isnan(expected["relevance_grades"]["a"]))
        with self.assertRaisesRegex(ClassifierError, "finite and from 0 through 1000"):
            validate_classifier_expected(expected)

    def test_schema_maximum_matches_the_relevance_grade_constant(self):
        schema = read_schema("classifier-expected.schema.json")
        ranking = next(
            branch for branch in schema["oneOf"] if branch["properties"]["kind"]["const"] == "ranking"
        )
        grades = ranking["properties"]["relevance_grades"]["additionalProperties"]
        self.assertEqual(grades["maximum"], MAX_RELEVANCE_GRADE)


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

    def test_ranking_metrics_stay_finite_at_the_maximum_relevance_grade(self):
        """2**1000 is representable but 2**1024 is not, so the cap is what keeps
        the exponential gain — and every metric derived from it — a real number."""
        expected = {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a", "b"],
            "relevance_grades": {"a": MAX_RELEVANCE_GRADE, "b": MAX_RELEVANCE_GRADE},
        }
        result = score_classifier_result(ranking_result(), expected, k=2)
        ndcg = result["scores"]["ndcg_at_2"]["value"]
        soft = result["scores"]["soft_score"]["value"]
        self.assertTrue(math.isfinite(ndcg))
        self.assertTrue(math.isfinite(soft))
        self.assertAlmostEqual(ndcg, 1.0)
        self.assertAlmostEqual(soft, 0.50 + 0.30)

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


def classifier_cell(
    scores: dict, *, agent: str = "classifier-seed", status: str = "pass"
) -> dict:
    return {
        "experiment": {"kind": "transform-classifier", "repetition": 1},
        "variant": {"provider": "ollama", "requested_model": "qwen3:4b", "thinking": "low", "agent": agent},
        "outcome": {
            "status": status,
            "failure_class": {"pass": "none", "invalid": "infrastructure"}.get(
                status, "benchmark"
            ),
            "scores": scores,
        },
        "execution": {"session": {"usage": {"output": 0}, "wall_clock_seconds": 0.0}},
    }


def boolean_scores(decision: bool, confidence: float, label: bool) -> dict:
    return score_classifier_result(
        boolean_result(decision, confidence),
        {"schema_version": 1, "kind": "boolean", "label": label},
    )["scores"]


def ranking_scores(*, k: int = 3, required_ids: list[str] | None = None, **expected: object) -> dict:
    """Real scorer output, so these tests cannot drift from the ranking semantics."""
    return score_classifier_result(
        ranking_result(),
        {
            "schema_version": 1,
            "kind": "ranking",
            "required_ids": ["a", "c"] if required_ids is None else required_ids,
            **expected,
        },
        k=k,
    )["scores"]


def table_rows(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("|")]


def table_widths(lines: list[str]) -> set[int]:
    """Cell counts per row: a GFM table renders only if every row agrees."""
    return {len(row.strip("|").split("|")) for row in table_rows(lines)}


class ClassifierReportTests(unittest.TestCase):
    def test_boolean_report_metrics_use_confusion_totals(self):
        summary = classifier_metrics(
            {
                "a": classifier_cell(boolean_scores(True, 0.8, True)),
                "b": classifier_cell(boolean_scores(True, 0.7, False)),
            }
        )
        self.assertEqual(summary["schema_validity_rate"], 1.0)
        self.assertEqual(summary["boolean"]["accuracy"], 0.5)
        self.assertEqual(summary["boolean"]["precision"], 0.5)
        self.assertEqual(summary["boolean"]["recall"], 1.0)

    def test_infrastructure_and_agent_failures_are_not_schema_failures(self):
        summary = classifier_metrics(
            {
                "ok": classifier_cell(boolean_scores(True, 0.8, True)),
                "infra": classifier_cell({}, status="invalid"),
                "agent": classifier_cell({}, status="invalid"),
            }
        )
        self.assertEqual(summary["total_cells"], 1)
        self.assertEqual(summary["valid_cells"], 1)
        self.assertEqual(summary["schema_validity_rate"], 1.0)
        self.assertEqual(summary["excluded_invalid_cells"], 2)

    def test_invalid_model_json_still_counts_against_schema_validity(self):
        summary = classifier_metrics(
            {
                "ok": classifier_cell(boolean_scores(True, 0.8, True)),
                "bad": classifier_cell(invalid_summary("not JSON")["scores"], status="fail"),
            }
        )
        self.assertEqual(summary["total_cells"], 2)
        self.assertEqual(summary["schema_validity_rate"], 0.5)

    def test_agent_axis_levels_are_reported_separately(self):
        summary = classifier_metrics(
            {
                "seed-1": classifier_cell(boolean_scores(True, 0.8, True), agent="classifier-seed"),
                "seed-2": classifier_cell(boolean_scores(False, 0.6, True), agent="classifier-seed"),
                "cand-1": classifier_cell(
                    boolean_scores(True, 0.9, True), agent="classifier-candidate"
                ),
                "cand-2": classifier_cell(
                    boolean_scores(True, 0.9, True), agent="classifier-candidate"
                ),
            }
        )
        by_agent = summary["by_agent"]
        self.assertEqual(set(by_agent), {"classifier-seed", "classifier-candidate"})
        self.assertEqual(by_agent["classifier-seed"]["boolean"]["accuracy"], 0.5)
        self.assertEqual(by_agent["classifier-candidate"]["boolean"]["accuracy"], 1.0)
        # the pooled summary survives alongside the per-agent breakdown
        self.assertEqual(summary["boolean"]["accuracy"], 0.75)

        report = classifier_by_agent_lines(by_agent)
        self.assertIn("### By agent", report)
        self.assertTrue(any(line.startswith("| classifier-candidate |") for line in report))
        self.assertTrue(any(line.startswith("| classifier-seed |") for line in report))
        self.assertEqual(len(table_widths(report)), 1)

    def test_ranking_metrics_average_every_score_the_scorer_emits(self):
        plain = ranking_scores(k=3)
        labeled = ranking_scores(k=3, binary_labels={"a": True, "b": False})
        summary = classifier_metrics(
            {"r1": classifier_cell(plain), "r2": classifier_cell(labeled)}
        )
        self.assertNotIn("boolean", summary)
        ranking = summary["ranking"]
        self.assertEqual(ranking["cells"], 2)
        self.assertEqual(ranking["recall_at_3"], 1.0)
        self.assertEqual(ranking["required_top_k"], 1.0)
        self.assertAlmostEqual(
            ranking["soft_score"],
            (plain["soft_score"]["value"] + labeled["soft_score"]["value"]) / 2,
        )
        # Brier is emitted only by the labeled cell, so it averages over the
        # cells that carry it rather than counting the other one as zero.
        self.assertAlmostEqual(ranking["brier"], labeled["brier"]["value"])
        # Schema validity is reported once above, not averaged in as a metric.
        self.assertNotIn("schema_validity", ranking)
        self.assertEqual(summary["schema_validity_rate"], 1.0)

    def test_the_by_agent_table_reads_ranking_metrics_when_there_is_no_boolean_block(self):
        summary = classifier_metrics(
            {
                "seed-1": classifier_cell(
                    ranking_scores(k=2), agent="classifier-seed", status="fail"
                ),
                "cand-1": classifier_cell(
                    ranking_scores(k=2, required_ids=["a"]), agent="classifier-candidate"
                ),
            }
        )
        by_agent = summary["by_agent"]
        self.assertNotIn("boolean", by_agent["classifier-seed"])
        self.assertEqual(by_agent["classifier-seed"]["ranking"]["recall_at_2"], 0.5)
        self.assertEqual(by_agent["classifier-candidate"]["ranking"]["recall_at_2"], 1.0)

        lines = classifier_by_agent_lines(by_agent)
        rows = table_rows(lines)
        header = [cell.strip() for cell in rows[0].strip("|").split("|")]
        self.assertEqual(header[:3], ["agent", "cells", "schema validity"])
        self.assertIn("recall_at_2", header)
        self.assertEqual(table_widths(lines), {len(header)})
        # No group has a boolean block, so every metric column comes from the
        # ranking fallback and none of them renders as a missing value.
        for row in rows[2:]:
            with self.subTest(row=row):
                self.assertNotIn("—", row)

    def test_a_mixed_boolean_and_ranking_table_still_lines_up(self):
        summary = classifier_metrics(
            {
                "seed-1": classifier_cell(
                    boolean_scores(True, 0.8, True), agent="classifier-seed"
                ),
                "cand-1": classifier_cell(ranking_scores(k=2), agent="classifier-candidate"),
            }
        )
        lines = classifier_by_agent_lines(summary["by_agent"])
        rows = table_rows(lines)
        header = [cell.strip() for cell in rows[0].strip("|").split("|")]
        self.assertIn("accuracy", header)
        self.assertIn("recall_at_2", header)
        self.assertEqual(table_widths(lines), {len(header)})
        # Each group fills only its own kind's columns; the rest are dashes.
        for row in rows[2:]:
            with self.subTest(row=row):
                self.assertIn("—", row)

    def test_the_report_renders_pooled_ranking_metrics(self):
        results = {
            "cell-a": classifier_cell(ranking_scores(k=3), agent="classifier-candidate"),
            "cell-b": classifier_cell(boolean_scores(True, 0.8, True), agent="classifier-seed"),
        }
        report = markdown_report(
            SimpleNamespace(
                id="exp-test-classifier", document={"question": "does the instruction help?"}
            ),
            results,
            {},
            {},
            {},
            classifier_metrics(results),
        )
        self.assertIn("## Classifier metrics", report)
        self.assertIn("### Boolean (pooled)", report)
        self.assertIn("### Ranking (pooled)", report)
        self.assertIn("### By agent", report)
        self.assertIn("| recall_at_3 |", report)

        section = report.split("## Classifier metrics", 1)[1].split("\n## ", 1)[0]
        blocks = section.split("### ")[1:]
        self.assertEqual(len(blocks), 3)
        for block in blocks:
            with self.subTest(block=block.splitlines()[0]):
                self.assertEqual(len(table_widths(block.splitlines())), 1)

    def test_an_all_invalid_agent_group_renders_a_valid_table(self):
        """No metric name survives, so the delimiter must shrink with the header."""
        summary = classifier_metrics(
            {
                "seed-1": classifier_cell(
                    invalid_summary("not JSON")["scores"],
                    agent="classifier-seed",
                    status="fail",
                )
            }
        )
        report = classifier_by_agent_lines(summary["by_agent"])
        rows = table_rows(report)
        self.assertEqual(len(rows), 3)  # header, delimiter, one agent row
        self.assertEqual(table_widths(report), {3})
        self.assertEqual(
            [cell.strip() for cell in rows[0].strip("|").split("|")],
            ["agent", "cells", "schema validity"],
        )
        self.assertEqual(rows[1], "| --- | ---: | ---: |")
        self.assertTrue(rows[2].startswith("| classifier-seed | 1 | 0/1 |"))


if __name__ == "__main__":
    unittest.main()
