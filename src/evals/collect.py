"""Execute one matrix cell and emit a normalized, schema-valid result bundle."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .classifier import (
    ClassifierError,
    invalid_summary,
    score_classifier_result,
    validate_classifier_expected,
)
from .container import AgentRunner, AgentRunRequest
from .matrix import Cell, spec_hash
from .pi import final_assistant_text, utc_now
from .resources import HarnessError
from .spec import Experiment, RunPaths, load_schema, validate_against

JsonDict = dict[str, Any]


class CellError(RuntimeError):
    """Raised on infrastructure failures the harness itself caused."""


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False
    )


def prepare_worktree(repo_root: Path, fixture: Path, destination: Path) -> None:
    completed = subprocess.run(
        ["bash", str(repo_root / "scripts" / "prepare_fixture.sh"), str(fixture), str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CellError(f"fixture preparation failed: {completed.stderr.strip()}")


def source_sha(repo_root: Path) -> str:
    completed = git(repo_root, "rev-parse", "HEAD")
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def build_cell_prompt(experiment: Experiment, cell: Cell) -> tuple[str, bool]:
    """Return (prompt, no_tools) for the task kind."""
    task = experiment.task
    if task.kind.startswith("transform"):
        assert task.input is not None
        input_text = task.input.read_text(encoding="utf-8", errors="replace")
        prompt = f"{task.prompt}\n\n---\nINPUT:\n{input_text}"
        no_tools = True
    else:
        prompt = task.prompt
        no_tools = False
    agent_instruction = experiment.agent_instructions.get(cell.agent)
    if agent_instruction:
        prompt = f"AGENT INSTRUCTIONS:\n{agent_instruction.strip()}\n\nTASK:\n{prompt}"
    return prompt, no_tools


def run_evaluator(experiment: Experiment, worktree: Path, cell_dir: Path) -> JsonDict:
    task = experiment.task
    assert task.evaluator is not None
    completed = subprocess.run(
        ["python3", str(task.evaluator), "--repo", str(worktree)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    (cell_dir / "evaluator.log").write_text(output, encoding="utf-8")

    if task.evaluator_format == "unittest":
        match = re.search(r"Ran (\d+) tests?", output)
        tests_run = int(match.group(1)) if match else 0
        scores = {"tests_pass": {"value": 1 if completed.returncode == 0 else 0, "max": 1}}
        summary = {
            "scores": scores,
            "pass": completed.returncode == 0,
            "tests_run": tests_run,
        }
    else:
        try:
            summary = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CellError(f"evaluator did not emit JSON: {exc.msg}") from exc
        if not isinstance(summary.get("scores"), dict) or "pass" not in summary:
            raise CellError("evaluator JSON must contain scores mapping and pass boolean")

    return write_evaluator_summary(cell_dir, summary, completed.returncode)


def write_evaluator_summary(cell_dir: Path, summary: JsonDict, exit_code: int) -> JsonDict:
    """Stamp the exit code and write the one evaluator.json format bundles read."""
    summary["exit_code"] = exit_code
    (cell_dir / "evaluator.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def run_classifier_evaluator(experiment: Experiment, output_text: str, cell_dir: Path) -> JsonDict:
    """Score a strict JSON classifier response against a hidden label file.

    Only the model's own output can fail the benchmark here. A hidden label
    file the harness cannot read or validate, and a contract schema it cannot
    find, are the harness's fault: they raise CellError so the cell records an
    infrastructure failure instead of charging the candidate for it.
    """
    task = experiment.task
    assert task.expected is not None
    try:
        expected = validate_classifier_expected(
            json.loads(task.expected.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ClassifierError, HarnessError) as error:
        raise CellError(f"cannot use expected label {task.expected}: {error}") from error

    try:
        summary = score_classifier_result(json.loads(output_text), expected)
        log = "classifier result is valid\n"
    except HarnessError as error:
        raise CellError(str(error)) from error
    except (json.JSONDecodeError, ClassifierError) as error:
        summary = invalid_summary(str(error))
        log = f"classifier result is invalid: {error}\n"
    (cell_dir / "evaluator.log").write_text(log, encoding="utf-8")
    return write_evaluator_summary(cell_dir, summary, 0 if summary["pass"] else 1)


def run_cell(
    experiment: Experiment,
    cell: Cell,
    runners: dict[str, AgentRunner],
    paths: RunPaths,
    *,
    run_id: str,
    observed_image_digest: str | None,
    agent_versions: dict[str, str | None],
    runtime_version: str | None,
    rerun: bool = False,
    keep_workspaces: bool = False,
) -> JsonDict:
    cell_dir = paths.cells / cell.cell_id
    result_path = cell_dir / "result.json"
    if result_path.exists() and not rerun:
        return {"cell_id": cell.cell_id, "status": "skipped", "reason": "existing result"}
    if cell_dir.exists():
        shutil.rmtree(cell_dir)
    cell_dir.mkdir(parents=True)

    task = experiment.task
    workspace = cell_dir / "workspace"
    infrastructure_error: str | None = None

    if task.kind.startswith("worktree"):
        assert task.fixture is not None
        prepare_worktree(experiment.repo_root, task.fixture, workspace)
    else:
        workspace.mkdir(parents=True)

    provider = experiment.registry.providers[cell.provider]
    runner = runners[provider.runner]

    agent_dir = cell_dir / "pi-agent"
    agent_dir.mkdir(parents=True)
    in_container = runner.renders_container_urls
    (agent_dir / "models.json").write_text(
        json.dumps(experiment.registry.pi_models_json(in_container=in_container), indent=2) + "\n",
        encoding="utf-8",
    )

    prompt, no_tools = build_cell_prompt(experiment, cell)

    env: dict[str, str] = {}
    if provider.api_key_env:
        import os

        value = os.environ.get(provider.api_key_env)
        if value is None:
            infrastructure_error = f"missing credential env {provider.api_key_env}"
        else:
            env[provider.api_key_env] = value

    if infrastructure_error is None:
        execution = runner.run(
            AgentRunRequest(
                image=experiment.container_image,
                digest=experiment.container_digest,
                workspace=workspace,
                agent_dir=agent_dir,
                provider=cell.provider,
                model=cell.model,
                thinking=cell.thinking,
                prompt=prompt,
                no_tools=no_tools,
                timeout_seconds=cell.timeout_seconds,
                env=env,
            ),
            cell_dir,
        )
    else:
        execution = {
            "agent_status": "error",
            "exit_code": None,
            "wall_clock_seconds": 0.0,
            "network": None,
            "usage": {},
            "chat_history": [],
            "response_models": [],
            "stop_reason": None,
            "parse_errors": [],
        }

    artifacts = ["request.json", "pi-output.jsonl", "pi-stderr.txt", "output.md"]
    evaluator_summary: JsonDict | None = None
    scores: JsonDict = {}
    evaluator_exit: int | None = None

    output_text = final_assistant_text(execution["chat_history"])
    (cell_dir / "output.md").write_text(output_text + "\n", encoding="utf-8")

    agent_ok = execution["agent_status"] == "success"

    # A cell whose agent never finished has no result to hold against the
    # contract: scoring its empty output would record a schema failure the
    # model did not commit.
    if task.kind == "transform-classifier" and infrastructure_error is None and agent_ok:
        try:
            evaluator_summary = run_classifier_evaluator(experiment, output_text, cell_dir)
        except (CellError, HarnessError) as error:
            infrastructure_error = str(error)
        else:
            scores = evaluator_summary["scores"]
            evaluator_exit = evaluator_summary["exit_code"]
            artifacts.extend(["evaluator.json", "evaluator.log"])

    if task.kind.startswith("worktree"):
        git(workspace, "add", "-N", ".")
        patch = git(workspace, "diff", "--binary", "HEAD", "--")
        if patch.returncode != 0:
            infrastructure_error = infrastructure_error or "patch_capture_failed"
        (cell_dir / "patch.diff").write_text(patch.stdout, encoding="utf-8")
        artifacts.append("patch.diff")

        if task.evaluator is not None and infrastructure_error is None:
            score_worktree = cell_dir / "score-workspace"
            assert task.fixture is not None
            prepare_worktree(experiment.repo_root, task.fixture, score_worktree)
            if patch.stdout.strip():
                applied = git(score_worktree, "apply", "--whitespace=nowarn", str(cell_dir / "patch.diff"))
                if applied.returncode != 0:
                    infrastructure_error = "patch_apply_failed"
            if infrastructure_error is None:
                try:
                    evaluator_summary = run_evaluator(experiment, score_worktree, cell_dir)
                    scores = evaluator_summary["scores"]
                    evaluator_exit = evaluator_summary["exit_code"]
                    artifacts.extend(["evaluator.json", "evaluator.log"])
                except CellError as error:
                    infrastructure_error = str(error)
            if not keep_workspaces:
                shutil.rmtree(score_worktree, ignore_errors=True)
    if not keep_workspaces:
        shutil.rmtree(workspace, ignore_errors=True)

    if infrastructure_error:
        status, failure_class = "invalid", "infrastructure"
    elif not agent_ok:
        status, failure_class = "invalid", "agent_execution"
    elif evaluator_summary is not None:
        status = "pass" if evaluator_summary.get("pass") else "fail"
        failure_class = "none" if status == "pass" else "benchmark"
    else:
        status, failure_class = "unscored", "none"

    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "cell_id": cell.cell_id,
        "spec_hash": spec_hash(experiment, cell),
        "run": {
            "origin": "local",
            "repository": None,
            "run_id": run_id,
            "run_attempt": "1",
            "source_sha": source_sha(experiment.repo_root),
            "collected_at": utc_now(),
        },
        "experiment": {
            "id": experiment.id,
            "kind": task.kind,
            "task_id": task.id,
            "repetition": cell.repetition,
        },
        "variant": {
            "harness": provider.runner,
            "agent": cell.agent,
            "provider": cell.provider,
            "requested_model": cell.model,
            "thinking": cell.thinking,
            "agent_version": agent_versions.get(provider.runner),
            "outfitter_version": None,
            "tools": [] if no_tools else ["default"],
        },
        "container": {
            "image": experiment.container_image,
            "digest": observed_image_digest,
            "runtime": runtime_version,
            "network": execution.get("network"),
        },
        "execution": {
            "agent_status": execution["agent_status"],
            "infrastructure_error": infrastructure_error,
            "exit_code": execution.get("exit_code"),
            "session": {
                "usage": execution.get("usage", {}),
                "response_models": execution.get("response_models", []),
                "stop_reason": execution.get("stop_reason"),
                "wall_clock_seconds": execution.get("wall_clock_seconds", 0.0),
            },
        },
        "outcome": {
            "status": status,
            "failure_class": failure_class,
            "scores": scores,
            "evaluator_exit_code": evaluator_exit,
        },
        "artifacts": sorted(artifacts),
    }
    validate_against(
        load_schema(experiment.repo_root, "result.schema.json"), result, f"result {cell.cell_id}"
    )
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    predicate = {
        "schemaVersion": SCHEMA_VERSION,
        "experimentId": experiment.id,
        "experimentKind": task.kind,
        "cellId": cell.cell_id,
        "specHash": result["spec_hash"],
        "taskId": task.id,
        "requestedModel": cell.model,
        "provider": cell.provider,
        "thinking": cell.thinking,
        "containerDigest": observed_image_digest,
        "sourceSha": result["run"]["source_sha"],
        "resultStatus": status,
    }
    (cell_dir / "attestation-predicate.json").write_text(
        json.dumps(predicate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_manifest(cell_dir)
    return {"cell_id": cell.cell_id, "status": status, "failure_class": failure_class}


def write_manifest(cell_dir: Path) -> None:
    manifest = cell_dir / "manifest.sha256"
    files = sorted(
        path
        for path in cell_dir.rglob("*")
        if path.is_file()
        and path != manifest
        and "workspace" not in path.relative_to(cell_dir).parts[:1]
        and "score-workspace" not in path.relative_to(cell_dir).parts[:1]
        and "pi-agent" not in path.relative_to(cell_dir).parts[:1]
    )
    manifest.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(cell_dir)}\n" for path in files),
        encoding="utf-8",
    )
