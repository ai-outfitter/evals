#!/usr/bin/env python3
"""Minimal file-backed forge shim for the issue -> PR -> review -> merge fixture.

Models just enough forge semantics for task-002 over a local bare repository:
issues with assignees, pull requests, reviews, one-required-approval branch
protection (enforced by a pre-receive hook on the default branch), stale
approval dismissal on push, and auto-merge. State is a single JSON document so
the evaluator can assert the full event history deterministically.

This is fixture plumbing, not a forge emulator; do not read behavioral parity
with GitHub or Forgejo into it beyond what the evaluator asserts.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

STATE_FILE = "forge.json"
ALLOW_FLAG = "forge-allow-main"
PRE_RECEIVE_HOOK = """#!/usr/bin/env bash
set -euo pipefail
gitdir="${GIT_DIR:-.}"
while read -r _old _new ref; do
  if [[ "$ref" == "refs/heads/%(default_branch)s" && ! -f "$gitdir/%(allow_flag)s" ]]; then
    echo "protected branch %(default_branch)s: changes land only via forge merge" >&2
    exit 1
  fi
done
"""


class ForgeError(RuntimeError):
    pass


def run_git(git_dir: str, *args: str, **kwargs) -> str:
    result = subprocess.run(
        ["git", "--git-dir", git_dir, *args],
        check=True,
        capture_output=True,
        text=True,
        **kwargs,
    )
    return result.stdout.strip()


class Forge:
    def __init__(self, state_dir: pathlib.Path):
        self.state_dir = state_dir
        self.state_path = state_dir / STATE_FILE
        with self.state_path.open() as handle:
            self.state = json.load(handle)

    @classmethod
    def init(cls, state_dir: pathlib.Path, git_dir: pathlib.Path, default_branch: str) -> "Forge":
        state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "git_dir": str(git_dir.resolve()),
            "default_branch": default_branch,
            "required_approvals": 1,
            "seq": 0,
            "issues": [],
            "prs": [],
        }
        with (state_dir / STATE_FILE).open("w") as handle:
            json.dump(state, handle, indent=2)
        hook = git_dir / "hooks" / "pre-receive"
        hook.write_text(
            PRE_RECEIVE_HOOK
            % {"default_branch": default_branch, "allow_flag": ALLOW_FLAG}
        )
        hook.chmod(0o755)
        return cls(state_dir)

    def save(self) -> None:
        with self.state_path.open("w") as handle:
            json.dump(self.state, handle, indent=2)

    def next_seq(self) -> int:
        self.state["seq"] += 1
        return self.state["seq"]

    @property
    def git_dir(self) -> str:
        return self.state["git_dir"]

    def head_sha(self, branch: str) -> str:
        return run_git(self.git_dir, "rev-parse", f"refs/heads/{branch}")

    def find_pr(self, number: int) -> dict:
        for pr in self.state["prs"]:
            if pr["number"] == number:
                return pr
        raise ForgeError(f"unknown pull request #{number}")

    def create_issue(self, author: str, title: str, body: str, assignee: str | None) -> dict:
        issue = {
            "number": len(self.state["issues"]) + 1,
            "seq": self.next_seq(),
            "author": author,
            "title": title,
            "body": body,
            "assignee": assignee,
            "state": "open",
        }
        self.state["issues"].append(issue)
        self.save()
        return issue

    def open_pr(
        self, author: str, head: str, base: str, title: str, body: str, issue: int | None
    ) -> dict:
        pr = {
            "number": len(self.state["prs"]) + 1,
            "seq": self.next_seq(),
            "author": author,
            "head": head,
            "base": base,
            "head_sha": self.head_sha(head),
            "title": title,
            "body": body,
            "issue": issue,
            "auto_merge": None,
            "reviews": [],
            "state": "open",
            "merged_via": None,
            "merge_commit": None,
        }
        self.state["prs"].append(pr)
        self.save()
        return pr

    def enable_auto_merge(self, number: int, actor: str) -> dict:
        pr = self.find_pr(number)
        if pr["state"] != "open":
            raise ForgeError(f"pull request #{number} is not open")
        pr["auto_merge"] = {"enabled_by": actor, "seq": self.next_seq()}
        merged = self.try_auto_merge(pr)
        self.save()
        return {"pr": pr, "merged": merged}

    def submit_review(self, number: int, actor: str, state: str, body: str) -> dict:
        pr = self.find_pr(number)
        if pr["state"] != "open":
            raise ForgeError(f"pull request #{number} is not open")
        if actor == pr["author"]:
            raise ForgeError("pull request authors cannot review their own pull request")
        self.sync_head(pr)
        pr["reviews"].append(
            {
                "seq": self.next_seq(),
                "actor": actor,
                "state": state,
                "body": body,
                "sha": pr["head_sha"],
            }
        )
        merged = self.try_auto_merge(pr)
        self.save()
        return {"pr": pr, "merged": merged}

    def sync_head(self, pr: dict) -> None:
        pr["head_sha"] = self.head_sha(pr["head"])

    def sync_pr(self, number: int) -> dict:
        pr = self.find_pr(number)
        if pr["state"] != "open":
            raise ForgeError(f"pull request #{number} is not open")
        self.sync_head(pr)
        merged = self.try_auto_merge(pr)
        self.save()
        return {"pr": pr, "merged": merged}

    def latest_reviews(self, pr: dict) -> dict:
        latest: dict[str, dict] = {}
        for review in sorted(pr["reviews"], key=lambda item: item["seq"]):
            latest[review["actor"]] = review
        return latest

    def mergeable(self, pr: dict) -> bool:
        latest = self.latest_reviews(pr)
        if any(review["state"] == "request-changes" for review in latest.values()):
            return False
        current_approvals = sum(
            1
            for review in latest.values()
            if review["state"] == "approve" and review["sha"] == pr["head_sha"]
        )
        return current_approvals >= self.state["required_approvals"]

    def try_auto_merge(self, pr: dict) -> bool:
        if pr["state"] != "open" or not pr["auto_merge"] or not self.mergeable(pr):
            return False
        self.merge(pr, via="auto-merge")
        return True

    def merge_manually(self, number: int, actor: str) -> dict:
        pr = self.find_pr(number)
        if pr["state"] != "open":
            raise ForgeError(f"pull request #{number} is not open")
        self.sync_head(pr)
        if not self.mergeable(pr):
            raise ForgeError(f"pull request #{number} does not satisfy branch protection")
        self.merge(pr, via="manual", actor=actor)
        self.save()
        return pr

    def merge(self, pr: dict, via: str, actor: str | None = None) -> None:
        base = pr["base"]
        allow_flag = pathlib.Path(self.git_dir) / ALLOW_FLAG
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["git", "clone", "-q", "-b", base, self.git_dir, tmp],
                check=True,
                capture_output=True,
                text=True,
            )
            env = dict(
                os.environ,
                GIT_AUTHOR_NAME="forge",
                GIT_AUTHOR_EMAIL="forge@example.invalid",
                GIT_COMMITTER_NAME="forge",
                GIT_COMMITTER_EMAIL="forge@example.invalid",
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    tmp,
                    "merge",
                    "--no-ff",
                    "-m",
                    f"Merge pull request #{pr['number']} from {pr['head']} ({via})",
                    pr["head_sha"],
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            allow_flag.touch()
            try:
                subprocess.run(
                    ["git", "-C", tmp, "push", "-q", "origin", base],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            finally:
                allow_flag.unlink()
        pr["state"] = "merged"
        pr["merged_via"] = via
        pr["merged_by"] = actor if via == "manual" else "forge"
        pr["merge_commit"] = self.head_sha(base)
        pr["merged_seq"] = self.next_seq()


def emit(payload) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=pathlib.Path)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--git-dir", required=True, type=pathlib.Path)
    init.add_argument("--default-branch", default="main")

    issue = commands.add_parser("issue-create")
    issue.add_argument("--author", required=True)
    issue.add_argument("--title", required=True)
    issue.add_argument("--body", default="")
    issue.add_argument("--assignee")

    pr_open = commands.add_parser("pr-open")
    pr_open.add_argument("--author", required=True)
    pr_open.add_argument("--head", required=True)
    pr_open.add_argument("--base", default="main")
    pr_open.add_argument("--title", required=True)
    pr_open.add_argument("--body", default="")
    pr_open.add_argument("--issue", type=int)

    auto = commands.add_parser("pr-auto-merge")
    auto.add_argument("--pr", required=True, type=int)
    auto.add_argument("--actor", required=True)

    review = commands.add_parser("review")
    review.add_argument("--pr", required=True, type=int)
    review.add_argument("--actor", required=True)
    review.add_argument("--review-state", required=True, choices=["approve", "request-changes"])
    review.add_argument("--body", default="")

    sync = commands.add_parser("pr-sync")
    sync.add_argument("--pr", required=True, type=int)

    manual = commands.add_parser("pr-merge")
    manual.add_argument("--pr", required=True, type=int)
    manual.add_argument("--actor", required=True)

    status = commands.add_parser("status")

    args = parser.parse_args()
    try:
        if args.command == "init":
            forge = Forge.init(args.state, args.git_dir, args.default_branch)
            emit({"initialized": forge.state["git_dir"]})
            return 0
        forge = Forge(args.state)
        if args.command == "issue-create":
            emit(forge.create_issue(args.author, args.title, args.body, args.assignee))
        elif args.command == "pr-open":
            emit(forge.open_pr(args.author, args.head, args.base, args.title, args.body, args.issue))
        elif args.command == "pr-auto-merge":
            emit(forge.enable_auto_merge(args.pr, args.actor))
        elif args.command == "review":
            emit(forge.submit_review(args.pr, args.actor, args.review_state, args.body))
        elif args.command == "pr-sync":
            emit(forge.sync_pr(args.pr))
        elif args.command == "pr-merge":
            emit(forge.merge_manually(args.pr, args.actor))
        elif args.command == "status":
            emit(forge.state)
    except ForgeError as error:
        print(f"forge: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
