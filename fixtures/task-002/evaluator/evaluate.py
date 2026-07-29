#!/usr/bin/env python3
"""Evaluator for the issue -> PR -> review -> merge full-cycle fixture.

Runs outside the agent worktrees, against the forge state directory and the
bare fixture repository, mirroring the task-001 evaluator pattern.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import unittest

LEAD = "lead"
IMPLEMENTER = "implementer"


def run_git(git_dir: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def suite_for(state: dict, git_dir: pathlib.Path):
    class CycleEvaluatorTests(unittest.TestCase):
        def issue(self) -> dict:
            issues = [item for item in state["issues"] if item["author"] == LEAD]
            self.assertEqual(len(issues), 1, "expected exactly one issue filed by lead")
            return issues[0]

        def merged_pr(self) -> dict:
            prs = [item for item in state["prs"] if item["state"] == "merged"]
            self.assertEqual(len(prs), 1, "expected exactly one merged pull request")
            return prs[0]

        def test_issue_created_and_assigned(self):
            issue = self.issue()
            self.assertEqual(issue["assignee"], IMPLEMENTER)
            text = f"{issue['title']}\n{issue['body']}"
            self.assertIn("PING", text)
            self.assertIn("auto-merge", text)

        def test_pr_authored_by_implementer(self):
            pr = self.merged_pr()
            self.assertEqual(pr["author"], IMPLEMENTER)
            self.assertEqual(pr["base"], state["default_branch"])
            self.assertEqual(pr["issue"], self.issue()["number"])

        def test_request_changes_then_approval_from_lead(self):
            pr = self.merged_pr()
            lead_reviews = sorted(
                (review for review in pr["reviews"] if review["actor"] == LEAD),
                key=lambda review: review["seq"],
            )
            states = [review["state"] for review in lead_reviews]
            self.assertIn("request-changes", states, "lead never requested changes")
            self.assertIn("approve", states, "lead never approved")
            first_request = states.index("request-changes")
            last_approve = len(states) - 1 - states[::-1].index("approve")
            self.assertLess(first_request, last_approve, "approval must follow request-changes")
            self.assertIn("PONG", lead_reviews[first_request]["body"])
            self.assertEqual(
                lead_reviews[last_approve]["sha"],
                pr["head_sha"],
                "approval must cover the merged head",
            )

        def test_merged_tree_contains_pong_not_ping(self):
            branch = state["default_branch"]
            names = run_git(git_dir, "ls-tree", "-r", "--name-only", branch).split()
            self.assertIn("PONG", names)
            self.assertNotIn("PING", names)
            self.assertEqual(run_git(git_dir, "show", f"{branch}:PONG").strip(), "ping")

        def test_history_records_rename(self):
            branch = state["default_branch"]
            log = run_git(
                git_dir,
                "log",
                "--follow",
                "-M",
                "--diff-filter=R",
                "--name-status",
                "--format=",
                branch,
                "--",
                "PONG",
            )
            self.assertRegex(
                log,
                re.compile(r"^R\d+\s+PING\s+PONG$", re.MULTILINE),
                "git history must record PING -> PONG as a rename",
            )

        def test_merged_via_auto_merge(self):
            pr = self.merged_pr()
            self.assertEqual(pr["merged_via"], "auto-merge", "merge must land via auto-merge")
            self.assertIsNotNone(pr["auto_merge"], "auto-merge was never enabled")
            self.assertEqual(pr["auto_merge"]["enabled_by"], IMPLEMENTER)
            parents = run_git(git_dir, "cat-file", "-p", pr["merge_commit"]).count("parent ")
            self.assertEqual(parents, 2, "merge commit must have two parents")

    return unittest.defaultTestLoader.loadTestsFromTestCase(CycleEvaluatorTests)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=pathlib.Path)
    parser.add_argument("--git-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    with (args.state / "forge.json").open() as handle:
        state = json.load(handle)
    result = unittest.TextTestRunner(verbosity=2).run(
        suite_for(state, args.git_dir.resolve())
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
