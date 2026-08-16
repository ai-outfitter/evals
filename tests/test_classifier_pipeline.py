"""exp-003 end to end: prompts, cell execution, blinding, and spec identity.

These tests drive the real experiment file rather than a fixture, because the
properties under test are properties of exp-003 itself: the hidden label must
stay out of both the agent prompt and the judge prompt, each agent instruction
must reach only its own cell, and a valid answer must score and propagate.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest
import uuid
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.bundle import clean_run
from evals.classifier import ClassifierError
from evals.cli import main
from evals.collect import CellError, run_cell, run_classifier_evaluator
from evals.container import StubAgentRunner
from evals.judge import build_judge_prompt, load_side_inputs
from evals.matrix import expand_matrix, spec_hash
from evals.reduce import run_reduce
from evals.resources import HarnessError, read_schema, schema_path
from evals.spec import SpecError, load_experiment, run_paths

REPO = pathlib.Path(__file__).resolve().parents[1]
EXP_003 = REPO / "experiments" / "exp-003-classifier-contract" / "experiment.yaml"

# The hidden label for this task is `true`, so this answer is correct.
VALID_RESULT = {
    "schema_version": 1,
    "kind": "boolean",
    "decision": True,
    "confidence": 0.9,
    "evidence_refs": ["event-log:17"],
}


class ValidClassifierRunner(StubAgentRunner):
    """A stub that answers the classifier contract correctly."""

    def run(self, request, artifact_dir):
        execution = super().run(request, artifact_dir)
        execution["chat_history"] = [
            {"role": "user", "text": request.prompt[:64]},
            {"role": "assistant", "text": json.dumps(VALID_RESULT)},
        ]
        return execution


class FailedAgentRunner(StubAgentRunner):
    """A stub whose agent never completed, so there is no answer to score."""

    def run(self, request, artifact_dir):
        execution = super().run(request, artifact_dir)
        execution["agent_status"] = "timeout"
        execution["exit_code"] = None
        execution["chat_history"] = []
        return execution


def experiment_and_cells():
    experiment = load_experiment(EXP_003)
    cells = expand_matrix(experiment)
    return experiment, cells


def collect(experiment, cell, paths, runner) -> dict:
    paths.cells.mkdir(parents=True, exist_ok=True)
    run_cell(
        experiment,
        cell,
        {"pi-container": runner, "claude-cli": runner},
        paths,
        run_id="test",
        observed_image_digest=None,
        agent_versions={"pi-container": "stub"},
        runtime_version="stub",
    )
    return json.loads((paths.cells / cell.cell_id / "result.json").read_text(encoding="utf-8"))


class JudgingInputValidationTests(unittest.TestCase):
    """A classifier spec may not route the answer key to the judge."""

    def write_variant(self, text: str) -> pathlib.Path:
        """A spec beside exp-003, so its relative agent and task paths resolve.

        `contained()` refuses anything outside the repo, so a system tempdir
        cannot host this; the name stays clear of the `experiment.yaml` glob
        that scripts/check.sh validates for success.
        """
        path = EXP_003.parent / f"pytest-{uuid.uuid4().hex[:8]}.yaml"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_evaluator_summary_in_judging_inputs_is_refused_at_load(self):
        text = EXP_003.read_text(encoding="utf-8")
        self.assertIn("inputs: [output]", text)
        path = self.write_variant(text.replace("inputs: [output]", "inputs: [output, evaluator_summary]"))
        with self.assertRaises(SpecError) as caught:
            load_experiment(path)
        message = str(caught.exception)
        self.assertIn("evaluator_summary", message)
        self.assertIn("judging.inputs", message)

    def test_an_unchanged_copy_still_loads(self):
        """The guard rejects the input, not merely any spec written elsewhere."""
        path = self.write_variant(EXP_003.read_text(encoding="utf-8"))
        self.assertEqual(load_experiment(path).judging_inputs, ["output"])


class ClassifierCliPipelineTests(unittest.TestCase):
    """`evals run -> judge -> prefer -> reduce -> bundle --validate`."""

    def setUp(self):
        self.run_id = f"pytest-{uuid.uuid4().hex[:8]}"
        self.experiment, _ = experiment_and_cells()
        self.paths = run_paths(REPO, self.experiment.id, self.run_id)
        self.addCleanup(clean_run, self.paths)

    def cli(self, *argv: str) -> str:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(list(argv))
        self.assertEqual(code, 0, stdout.getvalue())
        return stdout.getvalue()

    def assert_pair_isolates_the_agent(self, comparison: dict) -> None:
        """exp-003 varies only the agent, so every recorded pair must too."""
        left, right = (
            json.loads(
                (self.paths.cells / comparison[side] / "result.json").read_text(encoding="utf-8")
            )
            for side in ("left", "right")
        )
        self.assertNotEqual(left["variant"]["agent"], right["variant"]["agent"])
        for field in ("provider", "requested_model", "thinking"):
            self.assertEqual(left["variant"][field], right["variant"][field])
        self.assertEqual(left["experiment"]["repetition"], right["experiment"]["repetition"])

    def test_stub_run_scores_reduces_and_bundles(self):
        experiment = str(EXP_003)
        # The plain stub answers with prose, not a classifier result: exactly
        # the benchmark failure the contract is supposed to catch.
        self.cli("run", experiment, "--run-id", self.run_id, "--stub")

        result_paths = sorted(self.paths.cells.glob("*/result.json"))
        self.assertEqual(len(result_paths), 6)  # two agents x three repetitions
        for result_path in result_paths:
            with self.subTest(cell=result_path.parent.name):
                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(result["outcome"]["status"], "fail")
                self.assertEqual(result["outcome"]["failure_class"], "benchmark")
                self.assertEqual(result["outcome"]["scores"]["schema_validity"]["value"], 0)
                self.assertEqual(result["outcome"]["evaluator_exit_code"], 1)

                for artifact in ("evaluator.json", "evaluator.log"):
                    self.assertIn(artifact, result["artifacts"])
                    self.assertTrue((result_path.parent / artifact).is_file())
                summary = json.loads(
                    (result_path.parent / "evaluator.json").read_text(encoding="utf-8")
                )
                self.assertFalse(summary["pass"])
                self.assertEqual(summary["kind"], "invalid")
                self.assertTrue(summary["error"])
                self.assertIn(
                    "classifier result is invalid",
                    (result_path.parent / "evaluator.log").read_text(encoding="utf-8"),
                )

        # A failed contract is still a judgeable output, so the pairwise stages
        # run over the same cells. exp-003 has one model and one thinking level
        # and three repetitions, so there is exactly one pair per repetition.
        self.cli("judge", experiment, "--run-id", self.run_id, "--stub")
        verdict_paths = [
            path
            for path in sorted(self.paths.judgments.glob("*/*.json"))
            if path.parent.name != "pi-agent"
        ]
        self.assertEqual(len(verdict_paths), 3)
        for verdict_path in verdict_paths:
            with self.subTest(pair=verdict_path.stem):
                verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
                self.assertEqual(verdict["type"], "judge_verdict")
                self.assertIn(verdict["overall_preference"], {"left", "right", "tie"})
                self.assert_pair_isolates_the_agent(verdict["comparison"])

        self.cli("prefer", experiment, "--run-id", self.run_id, "--auto", "--rater", "test")
        preference_paths = sorted(self.paths.preferences.glob("*.json"))
        self.assertEqual(len(preference_paths), 3)
        for preference_path in preference_paths:
            with self.subTest(pair=preference_path.stem):
                preference = json.loads(preference_path.read_text(encoding="utf-8"))
                self.assertEqual(preference["rater"]["id"], "test")
                self.assert_pair_isolates_the_agent(preference["comparison"])

        self.cli("reduce", experiment, "--run-id", self.run_id)
        summary = json.loads((self.paths.root / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["judge_verdicts"], 3)
        self.assertEqual(summary["human_preferences"], 3)
        # Every pair isolates the agent, so that is the only axis either
        # pairwise tally can be broken out by.
        self.assertEqual(set(summary["judge_wins"]), {"agent"})
        self.assertEqual(set(summary["human_wins"]), {"agent"})
        metrics = summary["classifier_metrics"]
        self.assertEqual(metrics["total_cells"], 6)
        self.assertEqual(metrics["valid_cells"], 0)
        self.assertEqual(metrics["schema_validity_rate"], 0)
        self.assertEqual(metrics["excluded_invalid_cells"], 0)
        self.assertEqual(set(metrics["by_agent"]), {"classifier-seed", "classifier-candidate"})

        # Every cell failed the contract, so the agent table has no metric
        # columns at all: the header and delimiter still have to agree.
        report = (self.paths.root / "report.md").read_text(encoding="utf-8")
        self.assertIn("### By agent", report)
        # Stop at the next top-level heading: the judge and human preference
        # tables follow this section and have widths of their own.
        section = report.split("### By agent", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("|")]
        self.assertEqual(len(rows), 4)  # header, delimiter, one row per agent
        self.assertEqual({len(row.strip("|").split("|")) for row in rows}, {3})

        printed = self.cli("bundle", experiment, "--run-id", self.run_id, "--validate")
        reports = [json.loads(line) for line in printed.splitlines() if line.startswith("{")]
        self.assertEqual(len(reports), 6)
        self.assertTrue(all(report["ok"] for report in reports))
        # The evaluator artifacts have to travel with the bundle, or a replay
        # cannot check the score the report is built from.
        for report in reports:
            bundle = self.paths.root / "bundles" / f"{report['cell_id']}.tar.gz"
            with tarfile.open(bundle, "r:gz") as archive:
                members = set(archive.getnames())
            self.assertIn("evaluator.json", members)
            self.assertIn("evaluator.log", members)


class PromptIsolationTests(unittest.TestCase):
    """The agent prompt carries one instruction file and never the label."""

    @classmethod
    def setUpClass(cls):
        cls.experiment, cls.cells = experiment_and_cells()

    def test_each_agent_instruction_reaches_only_its_own_cell(self):
        from evals.collect import build_cell_prompt

        instructions = self.experiment.agent_instructions
        self.assertEqual(set(instructions), {"classifier-seed", "classifier-candidate"})
        for cell in self.cells:
            with self.subTest(agent=cell.agent):
                prompt, no_tools = build_cell_prompt(self.experiment, cell)
                self.assertTrue(no_tools)
                self.assertIn(instructions[cell.agent].strip(), prompt)
                for other, text in instructions.items():
                    if other != cell.agent:
                        self.assertNotIn(text.strip(), prompt)

    def test_hidden_expected_label_never_enters_the_agent_prompt(self):
        from evals.collect import build_cell_prompt

        expected_text = self.experiment.task.expected.read_text(encoding="utf-8")
        expected = json.loads(expected_text)
        for cell in self.cells:
            with self.subTest(agent=cell.agent):
                prompt, _ = build_cell_prompt(self.experiment, cell)
                self.assertNotIn(expected_text.strip(), prompt)
                self.assertNotIn(json.dumps(expected), prompt)
                self.assertNotIn("label", prompt)


class SpecHashTests(unittest.TestCase):
    """spec_hash covers every input that could change what a cell means."""

    def setUp(self):
        self.experiment, cells = experiment_and_cells()
        self.cell = next(cell for cell in cells if cell.agent == "classifier-seed")
        self.baseline = spec_hash(self.experiment, self.cell)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)

    def rewritten(self, field: str, content: str):
        path = self.root / f"{field}.json"
        path.write_text(content, encoding="utf-8")
        return replace(self.experiment, task=replace(self.experiment.task, **{field: path}))

    def test_input_content_change_alters_the_hash(self):
        original = self.experiment.task.input.read_text(encoding="utf-8")
        unchanged = self.rewritten("input", original)
        self.assertEqual(spec_hash(unchanged, self.cell), self.baseline)
        changed = self.rewritten("input", original.replace("safe mode", "standby mode"))
        self.assertNotEqual(spec_hash(changed, self.cell), self.baseline)

    def test_expected_label_change_alters_the_hash(self):
        original = self.experiment.task.expected.read_text(encoding="utf-8")
        unchanged = self.rewritten("expected", original)
        self.assertEqual(spec_hash(unchanged, self.cell), self.baseline)
        flipped = json.loads(original)
        flipped["label"] = not flipped["label"]
        changed = self.rewritten("expected", json.dumps(flipped, indent=2) + "\n")
        self.assertNotEqual(spec_hash(changed, self.cell), self.baseline)

    def test_agent_instruction_content_change_alters_the_hash(self):
        instructions = dict(self.experiment.agent_instructions)
        instructions["classifier-seed"] += "\nOne more sentence.\n"
        changed = replace(self.experiment, agent_instructions=instructions)
        self.assertNotEqual(spec_hash(changed, self.cell), self.baseline)

    def test_the_two_agent_levels_do_not_share_a_hash(self):
        candidate = replace(self.cell, agent="classifier-candidate")
        self.assertNotEqual(spec_hash(self.experiment, candidate), self.baseline)


class ClassifierSuccessPathTests(unittest.TestCase):
    """A valid answer scores, passes, and reaches the report."""

    def setUp(self):
        self.experiment, self.cells = experiment_and_cells()
        self.paths = run_paths(REPO, self.experiment.id, f"pytest-{uuid.uuid4().hex[:8]}")
        self.addCleanup(clean_run, self.paths)

    def test_valid_result_produces_a_passing_scored_cell(self):
        cell = self.cells[0]
        result = collect(self.experiment, cell, self.paths, ValidClassifierRunner())
        cell_dir = self.paths.cells / cell.cell_id

        summary = json.loads((cell_dir / "evaluator.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["pass"])
        self.assertEqual(summary["kind"], "boolean")
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["scores"]["schema_validity"]["value"], 1.0)
        self.assertEqual(summary["scores"]["accuracy"]["value"], 1.0)
        self.assertEqual(
            (cell_dir / "evaluator.log").read_text(encoding="utf-8"),
            "classifier result is valid\n",
        )

        self.assertEqual(result["outcome"]["status"], "pass")
        self.assertEqual(result["outcome"]["failure_class"], "none")
        self.assertEqual(result["outcome"]["evaluator_exit_code"], 0)
        # every evaluator score propagates into the cell result unchanged
        self.assertEqual(result["outcome"]["scores"], summary["scores"])
        self.assertIn("evaluator.json", result["artifacts"])
        self.assertIn("evaluator.log", result["artifacts"])

    def test_report_renders_the_agent_axis_for_valid_cells(self):
        for cell in self.cells:
            collect(self.experiment, cell, self.paths, ValidClassifierRunner())
        summary = run_reduce(self.experiment, self.paths)

        metrics = summary["classifier_metrics"]
        self.assertEqual(metrics["schema_validity_rate"], 1.0)
        self.assertEqual(metrics["excluded_invalid_cells"], 0)
        self.assertEqual(
            set(metrics["by_agent"]), {"classifier-seed", "classifier-candidate"}
        )
        for group in metrics["by_agent"].values():
            self.assertEqual(group["boolean"]["accuracy"], 1.0)

        report = (self.paths.root / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Classifier metrics", report)
        self.assertIn("### Boolean (pooled)", report)
        self.assertIn("### By agent", report)
        self.assertIn("| classifier-seed |", report)
        self.assertIn("| classifier-candidate |", report)

    def test_judge_prompt_excludes_the_label_and_the_evaluator_summary(self):
        cell = self.cells[0]
        collect(self.experiment, cell, self.paths, ValidClassifierRunner())
        evaluator = (self.paths.cells / cell.cell_id / "evaluator.json").read_text(
            encoding="utf-8"
        )
        self.assertIn("accuracy", evaluator)  # the leak exists on disk...

        self.assertEqual(self.experiment.judging_inputs, ["output"])
        sections = load_side_inputs(self.experiment, self.paths, cell)
        self.assertEqual(set(sections), {"output"})  # ...but is not selected

        prompt = build_judge_prompt(
            self.experiment, self.experiment.judges[0], {"A": sections, "B": sections}
        )
        expected = json.loads(self.experiment.task.expected.read_text(encoding="utf-8"))
        self.assertNotIn(json.dumps(expected), prompt)
        markers = (
            '"label"',  # the hidden expected label
            "evaluator_summary",
            "schema_validity",
            "accuracy",
            "brier",
            '"pass"',
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, prompt)

    def test_score_classifier_prints_the_scored_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "result.json").write_text(json.dumps(VALID_RESULT), encoding="utf-8")
            (root / "expected.json").write_text(
                self.experiment.task.expected.read_text(encoding="utf-8"), encoding="utf-8"
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    ["score-classifier", str(root / "result.json"), str(root / "expected.json")]
                )
        self.assertEqual(code, 0)
        printed = json.loads(stdout.getvalue())
        self.assertTrue(printed["pass"])
        self.assertEqual(printed["kind"], "boolean")
        self.assertEqual(printed["scores"]["accuracy"]["value"], 1.0)

    def test_score_classifier_prints_a_failed_summary_and_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "result.json").write_text(json.dumps(VALID_RESULT), encoding="utf-8")
            (root / "expected.json").write_text(
                json.dumps({"schema_version": 1, "kind": "boolean", "label": False}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    ["score-classifier", str(root / "result.json"), str(root / "expected.json")]
                )
        self.assertEqual(code, 1)
        printed = json.loads(stdout.getvalue())
        self.assertFalse(printed["pass"])
        self.assertEqual(printed["scores"]["accuracy"]["value"], 0.0)


class ClassifierFailureClassTests(unittest.TestCase):
    """Only the model's own output may fail the benchmark."""

    def setUp(self):
        self.experiment, self.cells = experiment_and_cells()
        self.cell = self.cells[0]
        self.paths = run_paths(REPO, self.experiment.id, f"pytest-{uuid.uuid4().hex[:8]}")
        self.addCleanup(clean_run, self.paths)

    def test_agent_execution_failure_is_not_a_schema_failure(self):
        result = collect(self.experiment, self.cell, self.paths, FailedAgentRunner())
        self.assertEqual(result["outcome"]["status"], "invalid")
        self.assertEqual(result["outcome"]["failure_class"], "agent_execution")
        self.assertEqual(result["outcome"]["scores"], {})
        self.assertNotIn("evaluator.json", result["artifacts"])
        self.assertFalse((self.paths.cells / self.cell.cell_id / "evaluator.json").exists())

    def test_corrupt_expected_label_is_an_infrastructure_failure(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        corrupt = pathlib.Path(directory.name) / "case.json"
        corrupt.write_text("{ not json", encoding="utf-8")
        experiment = replace(
            self.experiment, task=replace(self.experiment.task, expected=corrupt)
        )
        result = collect(experiment, self.cell, self.paths, ValidClassifierRunner())
        self.assertEqual(result["outcome"]["status"], "invalid")
        self.assertEqual(result["outcome"]["failure_class"], "infrastructure")
        self.assertIn("expected label", result["execution"]["infrastructure_error"])
        self.assertEqual(result["outcome"]["scores"], {})

    def test_missing_expected_label_raises_a_harness_error_not_a_score(self):
        missing = pathlib.Path(self.experiment.repo_root) / "tasks" / "does-not-exist.json"
        experiment = replace(
            self.experiment, task=replace(self.experiment.task, expected=missing)
        )
        cell_dir = self.paths.cells / self.cell.cell_id
        cell_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(CellError) as caught:
            run_classifier_evaluator(experiment, json.dumps(VALID_RESULT), cell_dir)
        self.assertIn("expected label", str(caught.exception))
        self.assertFalse((cell_dir / "evaluator.json").exists())

    def test_invalid_model_json_stays_a_benchmark_failure(self):
        result = collect(self.experiment, self.cell, self.paths, StubAgentRunner())
        self.assertEqual(result["outcome"]["status"], "fail")
        self.assertEqual(result["outcome"]["failure_class"], "benchmark")
        self.assertEqual(result["outcome"]["scores"]["schema_validity"]["value"], 0)


def classifier_cell(agent: str | None, *, correct: bool) -> dict:
    """One scored boolean classifier cell, with or without an agent axis."""
    variant = {"provider": "stub", "requested_model": "stub-1", "thinking": "off"}
    if agent is not None:
        variant["agent"] = agent
    scores = {
        "schema_validity": 1.0,
        "accuracy": float(correct),
        "true_positive": float(correct),
        "false_positive": 0.0,
        "false_negative": float(not correct),
        "true_negative": 0.0,
        "brier": 0.0 if correct else 1.0,
    }
    return {
        "experiment": {"kind": "transform-classifier"},
        "variant": variant,
        "outcome": {
            "status": "pass" if correct else "fail",
            "scores": {name: {"value": value, "max": 1.0} for name, value in scores.items()},
        },
    }


class ClassifierAggregationTests(unittest.TestCase):
    """The agent breakout is optional: not every classifier experiment has one."""

    def test_cells_without_an_agent_axis_still_pool(self):
        from evals.reduce import classifier_metrics

        metrics = classifier_metrics(
            {
                "cell-a": classifier_cell(None, correct=True),
                "cell-b": classifier_cell(None, correct=False),
            }
        )
        self.assertEqual(metrics["by_agent"], {})
        self.assertEqual(metrics["boolean"]["cells"], 2)
        self.assertEqual(metrics["schema_validity_rate"], 1.0)

    def test_the_report_renders_without_an_agent_section(self):
        from evals.reduce import classifier_by_agent_lines, classifier_metrics

        metrics = classifier_metrics({"cell-a": classifier_cell(None, correct=True)})
        self.assertEqual(classifier_by_agent_lines(metrics["by_agent"]), [])

    def test_an_agent_axis_is_still_broken_out(self):
        from evals.reduce import classifier_metrics

        metrics = classifier_metrics(
            {
                "cell-a": classifier_cell("seed", correct=True),
                "cell-b": classifier_cell("candidate", correct=False),
            }
        )
        self.assertEqual(set(metrics["by_agent"]), {"seed", "candidate"})
        self.assertEqual(metrics["by_agent"]["seed"]["boolean"]["accuracy"], 1.0)


class SchemaLookupTests(unittest.TestCase):
    """The contract schemas resolve without depending on the working directory."""

    def test_contract_schemas_resolve_from_an_unrelated_working_directory(self):
        import os

        original = pathlib.Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                schema = read_schema("classifier-result.schema.json")
            finally:
                os.chdir(original)
        self.assertEqual(schema["title"], "ClassifierResultV1")

    def test_contract_schemas_resolve_from_inside_the_package(self):
        """Resolution that depends on a checkout above the module dies in a wheel.

        tests/test_packaging.py proves this end to end against an installed
        distribution; this keeps the same property cheap to check in place.
        """
        import evals

        resolved = schema_path("classifier-result.schema.json").resolve()
        packaged = (pathlib.Path(evals.__file__).parent / "schemas").resolve()
        self.assertEqual(resolved.parent, packaged)

    def test_a_missing_schema_raises_a_harness_error_not_a_scoring_error(self):
        with self.assertRaises(HarnessError) as caught:
            schema_path("no-such-contract.schema.json")
        self.assertNotIsInstance(caught.exception, ClassifierError)

    def test_the_cli_reports_a_lookup_failure_instead_of_crashing(self):
        from unittest import mock

        from evals.classifier import _validator

        # the compiled-validator cache would otherwise hide the lookup entirely
        _validator.cache_clear()
        self.addCleanup(_validator.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "result.json").write_text(json.dumps(VALID_RESULT), encoding="utf-8")
            (root / "expected.json").write_text(
                json.dumps({"schema_version": 1, "kind": "boolean", "label": True}),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with mock.patch(
                "evals.resources.schema_path", side_effect=HarnessError("no schemas/ anywhere")
            ), contextlib.redirect_stderr(stderr):
                code = main(
                    ["score-classifier", str(root / "result.json"), str(root / "expected.json")]
                )
        self.assertEqual(code, 1)
        self.assertIn("no schemas/ anywhere", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
