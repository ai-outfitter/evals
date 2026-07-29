"""Unit tests for the task-002 forge shim semantics."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORGE = ROOT / "fixtures" / "task-002" / "forge" / "forge.py"
PREPARE = ROOT / "scripts" / "prepare_cycle_fixture.sh"
FIXTURE_REPO = ROOT / "fixtures" / "task-002" / "repo"


def run(*args, check=True, cwd=None):
    return subprocess.run(
        list(args), check=check, capture_output=True, text=True, cwd=cwd
    )


class ForgeShimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workdir = pathlib.Path(self.tmp.name) / "cycle"
        run("bash", str(PREPARE), str(FIXTURE_REPO), str(self.workdir))
        self.origin = self.workdir / "origin.git"
        self.state = self.workdir / "forge"
        self.checkout = self.workdir / "checkout"
        run("git", "clone", "-q", str(self.origin), str(self.checkout))
        run("git", "-C", str(self.checkout), "config", "user.name", "implementer")
        run(
            "git", "-C", str(self.checkout), "config",
            "user.email", "implementer@example.invalid",
        )

    def forge(self, *args, check=True):
        return run(
            sys.executable, str(FORGE), "--state", str(self.state), *args, check=check
        )

    def forge_json(self, *args):
        return json.loads(self.forge(*args).stdout)

    def push_branch(self, branch="topic", filename="PING"):
        run("git", "-C", str(self.checkout), "switch", "-q", "-c", branch)
        (self.checkout / filename).write_text("ping\n")
        run("git", "-C", str(self.checkout), "add", filename)
        run("git", "-C", str(self.checkout), "commit", "-qm", f"Add {filename}")
        run("git", "-C", str(self.checkout), "push", "-q", "origin", branch)

    def open_pr(self):
        self.push_branch()
        return self.forge_json(
            "pr-open", "--author", "implementer", "--head", "topic",
            "--title", "Add PING",
        )

    def test_direct_push_to_main_is_rejected(self):
        (self.checkout / "PING").write_text("ping\n")
        run("git", "-C", str(self.checkout), "add", "PING")
        run("git", "-C", str(self.checkout), "commit", "-qm", "Add PING")
        result = run(
            "git", "-C", str(self.checkout), "push", "origin", "main", check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected branch main", result.stderr)

    def test_auto_merge_waits_for_approval(self):
        pr = self.open_pr()
        enabled = self.forge_json(
            "pr-auto-merge", "--pr", str(pr["number"]), "--actor", "implementer"
        )
        self.assertFalse(enabled["merged"])
        approved = self.forge_json(
            "review", "--pr", str(pr["number"]), "--actor", "lead",
            "--review-state", "approve", "--body", "ok",
        )
        self.assertTrue(approved["merged"])
        self.assertEqual(approved["pr"]["merged_via"], "auto-merge")

    def test_request_changes_blocks_and_stale_approval_is_dismissed(self):
        pr = self.open_pr()
        self.forge_json(
            "pr-auto-merge", "--pr", str(pr["number"]), "--actor", "implementer"
        )
        blocked = self.forge_json(
            "review", "--pr", str(pr["number"]), "--actor", "lead",
            "--review-state", "request-changes", "--body", "PONG",
        )
        self.assertFalse(blocked["merged"])
        approved = self.forge_json(
            "review", "--pr", str(pr["number"]), "--actor", "lead",
            "--review-state", "approve", "--body", "ok",
        )
        self.assertTrue(approved["merged"])
        # A new PR whose approval predates the latest push must not merge.
        self.checkout2 = self.workdir / "checkout2"
        run("git", "clone", "-q", str(self.origin), str(self.checkout2))
        run("git", "-C", str(self.checkout2), "config", "user.name", "implementer")
        run(
            "git", "-C", str(self.checkout2), "config",
            "user.email", "implementer@example.invalid",
        )
        run("git", "-C", str(self.checkout2), "switch", "-q", "-c", "stale")
        (self.checkout2 / "OTHER").write_text("x\n")
        run("git", "-C", str(self.checkout2), "add", "OTHER")
        run("git", "-C", str(self.checkout2), "commit", "-qm", "Add OTHER")
        run("git", "-C", str(self.checkout2), "push", "-q", "origin", "stale")
        pr2 = self.forge_json(
            "pr-open", "--author", "implementer", "--head", "stale",
            "--title", "Add OTHER",
        )
        self.forge_json(
            "review", "--pr", str(pr2["number"]), "--actor", "lead",
            "--review-state", "approve", "--body", "ok",
        )
        (self.checkout2 / "OTHER").write_text("y\n")
        run("git", "-C", str(self.checkout2), "commit", "-aqm", "Update OTHER")
        run("git", "-C", str(self.checkout2), "push", "-q", "origin", "stale")
        synced = self.forge_json("pr-sync", "--pr", str(pr2["number"]))
        self.assertFalse(synced["merged"], "stale approval must not satisfy protection")
        self.forge_json(
            "pr-auto-merge", "--pr", str(pr2["number"]), "--actor", "implementer"
        )
        state = json.loads((self.state / "forge.json").read_text())
        self.assertEqual(state["prs"][1]["state"], "open")

    def test_author_cannot_review_own_pr(self):
        pr = self.open_pr()
        result = self.forge(
            "review", "--pr", str(pr["number"]), "--actor", "implementer",
            "--review-state", "approve", "--body", "self", check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_manual_merge_is_recorded_as_manual(self):
        pr = self.open_pr()
        self.forge_json(
            "review", "--pr", str(pr["number"]), "--actor", "lead",
            "--review-state", "approve", "--body", "ok",
        )
        merged = self.forge_json(
            "pr-merge", "--pr", str(pr["number"]), "--actor", "lead"
        )
        self.assertEqual(merged["merged_via"], "manual")


if __name__ == "__main__":
    unittest.main()
