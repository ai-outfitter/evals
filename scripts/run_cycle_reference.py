#!/usr/bin/env python3
"""Deterministic reference driver for the task-002 full-cycle fixture.

Scripts both roles (lead, implementer) through the exact scenario in
fixtures/task-002/scenario.yml without model calls, then runs the evaluator.
Used by scripts/check.sh to validate the forge shim, fixture preparation, and
evaluator plumbing end to end.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORGE = ROOT / "fixtures" / "task-002" / "forge" / "forge.py"
EVALUATOR = ROOT / "fixtures" / "task-002" / "evaluator" / "evaluate.py"
FIXTURE_REPO = ROOT / "fixtures" / "task-002" / "repo"


def run(*args: str, cwd: pathlib.Path | None = None) -> str:
    result = subprocess.run(
        list(args), check=True, capture_output=True, text=True, cwd=cwd
    )
    return result.stdout


def forge(state: pathlib.Path, *args: str) -> dict:
    return json.loads(run(sys.executable, str(FORGE), "--state", str(state), *args))


def clone_as(workdir: pathlib.Path, origin: pathlib.Path, actor: str) -> pathlib.Path:
    checkout = workdir / f"checkout-{actor}"
    run("git", "clone", "-q", str(origin), str(checkout))
    run("git", "-C", str(checkout), "config", "user.name", actor)
    run(
        "git", "-C", str(checkout), "config", "user.email", f"{actor}@example.invalid"
    )
    return checkout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    workdir = args.workdir

    run("bash", str(ROOT / "scripts" / "prepare_cycle_fixture.sh"), str(FIXTURE_REPO), str(workdir))
    workdir = workdir.resolve()
    origin = workdir / "origin.git"
    state = workdir / "forge"

    # Step 1 — lead files the issue and assigns it to implementer.
    issue = forge(
        state,
        "issue-create",
        "--author", "lead",
        "--title", "Add PING file via auto-merged PR",
        "--body",
        "Create a file named PING (content: ping) at the repository root via a "
        "PR; set the PR to auto-merge; respond to review feedback.",
        "--assignee", "implementer",
    )

    # Step 2 — implementer wakes on assignment: branch, PING, PR, auto-merge.
    impl = clone_as(workdir, origin, "implementer")
    run("git", "-C", str(impl), "switch", "-q", "-c", "issue-1-add-ping")
    (impl / "PING").write_text("ping\n")
    run("git", "-C", str(impl), "add", "PING")
    run("git", "-C", str(impl), "commit", "-qm", "Add PING")
    run("git", "-C", str(impl), "push", "-q", "origin", "issue-1-add-ping")
    pr = forge(
        state,
        "pr-open",
        "--author", "implementer",
        "--head", "issue-1-add-ping",
        "--base", "main",
        "--title", "Add PING",
        "--body", f"Closes #{issue['number']}",
        "--issue", str(issue["number"]),
    )
    forge(state, "pr-auto-merge", "--pr", str(pr["number"]), "--actor", "implementer")

    # Step 3 — lead requests changes.
    forge(
        state,
        "review",
        "--pr", str(pr["number"]),
        "--actor", "lead",
        "--review-state", "request-changes",
        "--body",
        "PONG — rename PING to PONG using git mv so the rename lands in git history",
    )

    # Step 4 — implementer renames via git mv and pushes.
    run("git", "-C", str(impl), "mv", "PING", "PONG")
    run("git", "-C", str(impl), "commit", "-qm", "Rename PING to PONG")
    run("git", "-C", str(impl), "push", "-q", "origin", "issue-1-add-ping")
    forge(state, "pr-sync", "--pr", str(pr["number"]))

    # Step 5 — lead verifies the rename on the new head and approves;
    # step 6 — auto-merge lands the pull request.
    lead = clone_as(workdir, origin, "lead")
    run("git", "-C", str(lead), "fetch", "-q", "origin", "issue-1-add-ping")
    names = run(
        "git", "-C", str(lead), "ls-tree", "--name-only", "FETCH_HEAD"
    ).split()
    if "PONG" not in names or "PING" in names:
        print("lead verification failed: rename not present on PR head", file=sys.stderr)
        return 1
    approval = forge(
        state,
        "review",
        "--pr", str(pr["number"]),
        "--actor", "lead",
        "--review-state", "approve",
        "--body", "Rename verified.",
    )
    if not approval["merged"]:
        print("auto-merge did not land the pull request", file=sys.stderr)
        return 1

    return subprocess.run(
        [
            sys.executable,
            str(EVALUATOR),
            "--state", str(state),
            "--git-dir", str(origin),
        ]
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
