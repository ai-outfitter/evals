"""End-to-end stub pipeline: run -> judge -> prefer(auto) -> reduce -> bundle."""

import json
import pathlib
import sys
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.bundle import clean_run, run_bundle, run_validate
from evals.cli import main

REPO = pathlib.Path(__file__).resolve().parents[1]
EXP_002 = REPO / "experiments" / "exp-002-transcript-summary" / "experiment.yaml"
EXP_001 = REPO / "experiments" / "exp-001-tasksmd-thinking" / "experiment.yaml"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.run_id = f"pytest-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        for experiment_id in ("exp-002-transcript-summary", "exp-001-tasksmd-thinking"):
            from evals.spec import run_paths

            clean_run(run_paths(REPO, experiment_id, self.run_id))

    def cli(self, *argv: str) -> int:
        return main(list(argv))

    def test_transform_pipeline(self):
        exp = str(EXP_002)
        self.assertEqual(self.cli("run", exp, "--run-id", self.run_id, "--stub"), 0)

        run_dir = REPO / "runs" / "exp-002-transcript-summary" / self.run_id
        results = sorted(run_dir.glob("cells/*/result.json"))
        self.assertEqual(len(results), 6)
        first = json.loads(results[0].read_text())
        self.assertEqual(first["outcome"]["status"], "unscored")
        self.assertEqual(first["schema_version"], 2)

        # resume: second run skips everything
        self.assertEqual(self.cli("run", exp, "--run-id", self.run_id, "--stub"), 0)

        self.assertEqual(self.cli("judge", exp, "--run-id", self.run_id, "--stub"), 0)
        verdicts = sorted(run_dir.glob("judgments/*/*.json"))
        verdicts = [path for path in verdicts if path.parent.name != "pi-agent"]
        self.assertTrue(verdicts)
        verdict = json.loads(verdicts[0].read_text())
        self.assertEqual(verdict["type"], "judge_verdict")
        self.assertIn(verdict["overall_preference"], {"left", "right", "tie"})

        self.assertEqual(
            self.cli("prefer", exp, "--run-id", self.run_id, "--auto", "--rater", "test"), 0
        )
        preferences = sorted(run_dir.glob("preferences/*.json"))
        self.assertTrue(preferences)
        preference = json.loads(preferences[0].read_text())
        self.assertEqual(preference["rater"]["id"], "test")

        self.assertEqual(self.cli("reduce", exp, "--run-id", self.run_id), 0)
        self.assertTrue((run_dir / "report.md").is_file())
        summary = json.loads((run_dir / "results.json").read_text())
        self.assertEqual(len(summary["cells"]), 6)

        from evals.spec import run_paths

        paths = run_paths(REPO, "exp-002-transcript-summary", self.run_id)
        manifest = run_bundle(paths, EXP_002)
        self.assertEqual(len(manifest["bundles"]), 6)
        reports = run_validate(paths)
        self.assertTrue(all(report["ok"] for report in reports))

    def test_worktree_pipeline_scores_cells(self):
        exp = str(EXP_001)
        self.assertEqual(
            self.cli(
                "run",
                exp,
                "--run-id",
                self.run_id,
                "--stub",
                "--filter",
                "model=ollama/qwen3:4b",
                "--filter",
                "thinking=off",
            ),
            0,
        )
        run_dir = REPO / "runs" / "exp-001-tasksmd-thinking" / self.run_id
        results = sorted(run_dir.glob("cells/*/result.json"))
        self.assertEqual(len(results), 1)
        result = json.loads(results[0].read_text())
        # the stub edits nothing useful, so the evaluator scores 0/5 -> fail
        self.assertEqual(result["outcome"]["status"], "fail")
        self.assertEqual(result["outcome"]["failure_class"], "benchmark")
        self.assertEqual(result["outcome"]["scores"]["tasks_completed"]["max"], 5)
        self.assertIn("patch.diff", result["artifacts"])


if __name__ == "__main__":
    unittest.main()
