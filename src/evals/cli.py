"""evals CLI: validate | matrix | run | judge | prefer | reduce | bundle | smoke."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .bundle import BundleError, run_bundle, run_validate
from .collect import run_cell
from .container import (
    ContainerError,
    PodmanAgentRunner,
    StubAgentRunner,
    runtime_version,
    verify_digest,
)
from .judge import run_judges
from .matrix import apply_filters, expand_matrix, parse_filters, spec_hash
from .prefer import run_preferences
from .reduce import run_reduce
from .spec import SpecError, load_experiment, run_paths


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("experiment", type=Path, help="path to experiment.yaml")
    parser.add_argument("--run-id", default="default", help="run directory name (default: default)")
    parser.add_argument("--stub", action="store_true", help="deterministic no-model, no-container mode")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evals")
    sub = parser.add_subparsers(dest="command", required=True)

    sub_validate = sub.add_parser("validate", help="validate an experiment and its referenced specs")
    sub_validate.add_argument("experiment", type=Path)

    sub_matrix = sub.add_parser("matrix", help="print the expanded cell matrix")
    sub_matrix.add_argument("experiment", type=Path)
    sub_matrix.add_argument("--github-json", action="store_true", help="emit a strategy.matrix include list")

    sub_run = sub.add_parser("run", help="execute the matrix cells")
    add_common(sub_run)
    sub_run.add_argument("--filter", action="append", default=[], help="model=…|thinking=…|agent=…")
    sub_run.add_argument("--rerun", action="store_true")
    sub_run.add_argument("--keep-workspaces", action="store_true")

    sub_judge = sub.add_parser("judge", help="run blinded pairwise judge agents")
    add_common(sub_judge)
    sub_judge.add_argument("--rerun", action="store_true")

    sub_prefer = sub.add_parser("prefer", help="record blinded human preferences")
    add_common(sub_prefer)
    sub_prefer.add_argument("--rater", default=os.environ.get("USER", "anonymous"))
    sub_prefer.add_argument("--auto", action="store_true", help="answer tie for every pair (CI)")

    sub_reduce = sub.add_parser("reduce", help="aggregate results into report.md")
    add_common(sub_reduce)

    sub_bundle = sub.add_parser("bundle", help="write reproducible cell bundles")
    add_common(sub_bundle)
    sub_bundle.add_argument("--validate", action="store_true")

    sub_smoke = sub.add_parser("smoke", help="probe one model through the container")
    sub_smoke.add_argument("experiment", type=Path)
    sub_smoke.add_argument("model_ref", help="provider/model to probe")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args)
    except (SpecError, ContainerError, BundleError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def dispatch(args: argparse.Namespace) -> int:
    experiment = load_experiment(args.experiment)

    if args.command == "validate":
        print(f"ok: {experiment.id} ({len(expand_matrix(experiment))} cells)")
        return 0

    if args.command == "matrix":
        cells = expand_matrix(experiment)
        if args.github_json:
            include = [
                {
                    "cell_id": cell.cell_id,
                    "model_ref": cell.model_ref,
                    "thinking": cell.thinking,
                    "agent": cell.agent,
                    "repetition": cell.repetition,
                }
                for cell in cells
            ]
            print(json.dumps({"include": include}))
        else:
            for cell in cells:
                print(
                    f"{cell.cell_id}  timeout={cell.timeout_seconds:.0f}s  "
                    f"spec={spec_hash(experiment, cell)[:19]}"
                )
            print(f"{len(cells)} cells")
        return 0

    paths = run_paths(experiment.repo_root, experiment.id, getattr(args, "run_id", "default"))

    if args.command == "run":
        cells = apply_filters(expand_matrix(experiment), parse_filters(args.filter))
        if not cells:
            print("no cells match the filters", file=sys.stderr)
            return 1
        if args.stub:
            runner: PodmanAgentRunner | StubAgentRunner = StubAgentRunner()
            digest = experiment.container_digest
            pi_version = runner.pi_version(experiment.container_image, digest)
            observed_runtime = "stub"
        else:
            runner = PodmanAgentRunner()
            digest = verify_digest(experiment.container_image, experiment.container_digest)
            pi_version = runner.pi_version(experiment.container_image, experiment.container_digest)
            observed_runtime = runtime_version()
        paths.cells.mkdir(parents=True, exist_ok=True)
        failures = 0
        for index, cell in enumerate(cells, start=1):
            print(f"[{index}/{len(cells)}] {cell.cell_id} ...", flush=True)
            outcome = run_cell(
                experiment,
                cell,
                runner,
                paths,
                run_id=args.run_id,
                observed_image_digest=digest,
                pi_version=pi_version,
                runtime_version=observed_runtime,
                rerun=args.rerun,
                keep_workspaces=args.keep_workspaces,
            )
            print(f"    -> {outcome['status']}" + (f" ({outcome.get('reason')})" if outcome.get("reason") else ""))
            if outcome["status"] in {"invalid"}:
                failures += 1
        return 1 if failures == len(cells) else 0

    if args.command == "judge":
        cells = expand_matrix(experiment)
        runner = StubAgentRunner() if args.stub else PodmanAgentRunner()
        if not args.stub:
            verify_digest(experiment.container_image, experiment.container_digest)
        records = run_judges(
            experiment, paths, cells, runner, run_id=args.run_id, rerun=args.rerun
        )
        for record in records:
            print(f"{record['judge']}  {record['pair']}  {record['status']}")
        invalid = sum(1 for record in records if record["status"] == "invalid")
        print(f"{len(records)} verdicts ({invalid} invalid)")
        return 0

    if args.command == "prefer":
        cells = expand_matrix(experiment)
        records = run_preferences(
            experiment, paths, cells, run_id=args.run_id, rater=args.rater, auto=args.auto
        )
        recorded = sum(1 for record in records if record["status"] == "ok")
        print(f"{recorded} recorded, {len(records) - recorded} already present")
        return 0

    if args.command == "reduce":
        summary = run_reduce(experiment, paths)
        print(f"report: {paths.root / 'report.md'}")
        print(
            f"cells={len(summary['cells'])} verdicts={summary['judge_verdicts']} "
            f"preferences={summary['human_preferences']}"
        )
        return 0

    if args.command == "bundle":
        manifest = run_bundle(paths, experiment.path)
        print(f"{len(manifest['bundles'])} bundles under {paths.root / 'bundles'}")
        if args.validate:
            for report in run_validate(paths):
                print(json.dumps(report))
        return 0

    if args.command == "smoke":
        from .container import AgentRunRequest
        from .pi import build_pi_command, final_assistant_text

        provider, model = experiment.registry.resolve(args.model_ref)
        runner = PodmanAgentRunner()
        digest = verify_digest(experiment.container_image, experiment.container_digest)
        import tempfile

        with tempfile.TemporaryDirectory(prefix="evals-smoke-") as temp:
            workspace = Path(temp) / "workspace"
            agent_dir = Path(temp) / "pi-agent"
            workspace.mkdir()
            agent_dir.mkdir()
            (agent_dir / "models.json").write_text(
                json.dumps(experiment.registry.pi_models_json(in_container=True), indent=2) + "\n"
            )
            env = {}
            if provider.api_key_env and os.environ.get(provider.api_key_env):
                env[provider.api_key_env] = os.environ[provider.api_key_env]
            execution = runner.run(
                AgentRunRequest(
                    image=experiment.container_image,
                    digest=experiment.container_digest,
                    workspace=workspace,
                    agent_dir=agent_dir,
                    pi_args=build_pi_command(
                        provider=provider.name,
                        model=model,
                        thinking="off",
                        prompt="Reply with exactly: ok",
                        no_tools=True,
                    ),
                    timeout_seconds=120,
                    env=env,
                ),
                Path(temp) / "artifacts",
            )
        print(
            json.dumps(
                {
                    "agent_status": execution["agent_status"],
                    "response": final_assistant_text(execution["chat_history"]),
                    "response_models": execution["response_models"],
                    "image_digest": digest,
                }
            )
        )
        return 0 if execution["agent_status"] == "success" else 1

    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
