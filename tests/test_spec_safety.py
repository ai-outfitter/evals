"""Specs are untrusted input: they must not name paths outside the repo."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.container import ClaudeAgentRunner, PodmanAgentRunner, StubAgentRunner
from evals.spec import SpecError, load_experiment

REPO = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"


class PathContainmentTests(unittest.TestCase):

    def test_task_outside_repo_is_rejected(self):
        target = FIXTURES / "exp-escape-task.yaml"
        try:
            target.write_text(
                (FIXTURES / "exp-transform.yaml")
                .read_text()
                .replace("task: transform-echo/task.yaml", "task: ../../../../etc/task.yaml"),
                encoding="utf-8",
            )
            with self.assertRaises(SpecError) as caught:
                load_experiment(target)
            self.assertIn("outside the repository", str(caught.exception))
        finally:
            target.unlink(missing_ok=True)

    def test_providers_outside_repo_is_rejected(self):
        target = FIXTURES / "exp-escape-providers.yaml"
        try:
            target.write_text(
                (FIXTURES / "exp-transform.yaml")
                .read_text()
                .replace("providers: ../../models.yaml", "providers: /etc/models.yaml"),
                encoding="utf-8",
            )
            with self.assertRaises(SpecError) as caught:
                load_experiment(target)
            self.assertIn("outside the repository", str(caught.exception))
        finally:
            target.unlink(missing_ok=True)

    def test_task_input_outside_repo_is_rejected(self):
        task_dir = FIXTURES / "escape-echo"
        task_dir.mkdir(exist_ok=True)
        task = task_dir / "task.yaml"
        experiment = FIXTURES / "exp-escape-input.yaml"
        try:
            task.write_text(
                "\n".join(
                    [
                        "schema_version: 2",
                        "id: escape-echo",
                        "kind: transform-judge",
                        "input: ../../../../../etc/hostname",
                        "prompt: |",
                        "  Summarize the input.",
                        "metrics:",
                        "  - accuracy",
                        "untrusted: true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            experiment.write_text(
                (FIXTURES / "exp-transform.yaml")
                .read_text()
                .replace("task: transform-echo/task.yaml", "task: escape-echo/task.yaml"),
                encoding="utf-8",
            )
            with self.assertRaises(SpecError) as caught:
                load_experiment(experiment)
            self.assertIn("outside the repository", str(caught.exception))
        finally:
            task.unlink(missing_ok=True)
            task_dir.rmdir()
            experiment.unlink(missing_ok=True)

    def test_committed_fixtures_still_load(self):
        for name in ("exp-transform.yaml", "exp-worktree.yaml"):
            self.assertTrue(load_experiment(FIXTURES / name).id)


class RunnerCapabilityTests(unittest.TestCase):
    def test_capabilities_are_explicit(self):
        podman, claude, stub = PodmanAgentRunner(), ClaudeAgentRunner(), StubAgentRunner()
        # Only Pi-in-the-container resolves provider URLs from inside the image.
        self.assertTrue(podman.renders_container_urls)
        self.assertFalse(claude.renders_container_urls)
        self.assertFalse(stub.renders_container_urls)
        # Only the container runner requires the image to exist.
        self.assertTrue(podman.needs_image)
        self.assertFalse(claude.needs_image)
        self.assertFalse(stub.needs_image)

    def test_every_runner_reports_a_version_method(self):
        for runner in (PodmanAgentRunner(), ClaudeAgentRunner(), StubAgentRunner()):
            self.assertTrue(callable(runner.agent_version))


if __name__ == "__main__":
    unittest.main()
