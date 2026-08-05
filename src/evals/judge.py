"""Blinded pairwise judging of completed cells by review agents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .container import AgentRunner, AgentRunRequest, StubAgentRunner
from .matrix import Cell, comparison_pairs
from .pi import utc_now, validate_json_schema_output
from .spec import Experiment, Judge, RunPaths, load_schema, validate_against

JsonDict = dict[str, Any]

INPUT_TRUNCATION = 8000

VERDICT_SCHEMA: JsonDict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["metrics", "overall", "rationale"],
    "properties": {
        "metrics": {"type": "object", "additionalProperties": {"enum": ["A", "B", "tie"]}},
        "overall": {"enum": ["A", "B", "tie"]},
        "rationale": {"type": "string"},
    },
}


def blinding_seed(run_id: str, actor: str, left: str, right: str) -> int:
    digest = hashlib.sha256(f"{run_id}:{actor}:{left}:{right}".encode()).hexdigest()
    return int(digest[:12], 16)


def presented_order(seed: int) -> list[str]:
    return ["left", "right"] if seed % 2 == 0 else ["right", "left"]


def judgeable_cells(experiment: Experiment, paths: RunPaths, cells: list[Cell]) -> list[Cell]:
    usable = []
    for cell in cells:
        result_path = paths.cells / cell.cell_id / "result.json"
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result["outcome"]["status"] in {"pass", "fail", "unscored"}:
            usable.append(cell)
    return usable


def load_side_inputs(experiment: Experiment, paths: RunPaths, cell: Cell) -> dict[str, str]:
    cell_dir = paths.cells / cell.cell_id
    sections: dict[str, str] = {}
    for input_kind in experiment.judging_inputs:
        if input_kind == "diff":
            path = cell_dir / "patch.diff"
            sections["diff"] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        elif input_kind in {"final_message", "output"}:
            path = cell_dir / "output.md"
            sections[input_kind] = (
                path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            )
        elif input_kind == "evaluator_summary":
            path = cell_dir / "evaluator.json"
            if path.exists():
                summary = json.loads(path.read_text(encoding="utf-8"))
                sections["evaluator_summary"] = json.dumps(
                    {"scores": summary.get("scores"), "pass": summary.get("pass")}, indent=2
                )
            else:
                sections["evaluator_summary"] = ""
    return sections


def truncate(text: str) -> str:
    if len(text) <= INPUT_TRUNCATION:
        return text
    return text[:INPUT_TRUNCATION] + "\n[... truncated ...]"


def build_judge_prompt(
    experiment: Experiment,
    judge: Judge,
    label_inputs: dict[str, dict[str, str]],
) -> str:
    metric_lines = "\n".join(
        f"- {metric['id']}: {metric['description']}" for metric in judge.rubric.metrics
    )
    sections = []
    for label in ("A", "B"):
        for input_kind, text in label_inputs[label].items():
            sections.append(f"### Response {label} — {input_kind}\n\n{truncate(text) or '(empty)'}")
    section_text = "\n\n".join(sections)
    schema_text = json.dumps(VERDICT_SCHEMA, indent=2, sort_keys=True)
    metric_ids = [metric["id"] for metric in judge.rubric.metrics]

    parts = [
        "You are an impartial evaluation judge. Two anonymous candidate responses to the same task are labeled A and B. Compare them on each metric. Do not guess which system produced them; judge only the content shown.",
        "Everything you need is in this message. Do not use tools, do not read files, and do not ask for more material.",
        judge.rubric.instructions,
        f"Metrics:\n{metric_lines}",
        f"## Task given to both candidates\n\n{truncate(experiment.task.prompt)}",
    ]
    # Source material is what makes fidelity judgeable: without it a judge can
    # only guess whether a claim was supported.
    source = source_material(experiment)
    if source:
        parts.append(f"## Source material the candidates were given\n\n{truncate(source)}")
    parts.extend(
        [
            f"## Candidates\n\n{section_text}",
            "Return exactly one JSON object and nothing else — no preamble, no explanation outside the JSON, no code fences around anything but the object. It must have a verdict of \"A\", \"B\", or \"tie\" for every metric listed above, an overall verdict, and a one-paragraph rationale.",
            f"Required metric keys: {json.dumps(metric_ids)}",
            f"JSON Schema:\n{schema_text}",
        ]
    )
    return "\n\n".join(parts)


def source_material(experiment: Experiment) -> str:
    """The task's own input, so judges can check claims against the source."""
    task = experiment.task
    if task.input is not None and task.input.is_file():
        return task.input.read_text(encoding="utf-8", errors="replace")
    if task.fixture is not None:
        tasks_file = task.fixture / "TASKS.md"
        if tasks_file.is_file():
            return tasks_file.read_text(encoding="utf-8", errors="replace")
    return ""


def unblind(value: str, order: list[str]) -> str:
    if value == "tie":
        return "tie"
    label_map = {"A": order[0], "B": order[1]}
    return label_map[value]


def run_judges(
    experiment: Experiment,
    paths: RunPaths,
    cells: list[Cell],
    runners: dict[str, AgentRunner],
    *,
    run_id: str,
    rerun: bool = False,
) -> list[JsonDict]:
    usable = judgeable_cells(experiment, paths, cells)
    pairs = comparison_pairs(usable, experiment.independent_variables)
    verdict_schema = load_schema(experiment.repo_root, "judge-verdict.schema.json")
    records: list[JsonDict] = []

    for judge in experiment.judges:
        provider, model = experiment.registry.resolve(judge.model_ref)
        runner = runners[provider.runner]
        judge_dir = paths.judgments / judge.id
        agent_dir = judge_dir / "pi-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        in_container = runner.renders_container_urls
        (agent_dir / "models.json").write_text(
            json.dumps(experiment.registry.pi_models_json(in_container=in_container), indent=2)
            + "\n",
            encoding="utf-8",
        )
        workspace = judge_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        for left, right in pairs:
            verdict_path = judge_dir / f"{left.cell_id}__{right.cell_id}.json"
            if verdict_path.exists() and not rerun:
                records.append({"judge": judge.id, "pair": verdict_path.stem, "status": "skipped"})
                continue

            seed = blinding_seed(run_id, judge.id, left.cell_id, right.cell_id)
            order = presented_order(seed)
            by_side = {
                "left": load_side_inputs(experiment, paths, left),
                "right": load_side_inputs(experiment, paths, right),
            }
            label_inputs = {"A": by_side[order[0]], "B": by_side[order[1]]}
            prompt = build_judge_prompt(experiment, judge, label_inputs)

            env: dict[str, str] = {}
            if provider.api_key_env:
                import os

                value = os.environ.get(provider.api_key_env)
                if value is not None:
                    env[provider.api_key_env] = value

            artifact_dir = judge_dir / f"{left.cell_id}__{right.cell_id}.artifacts"
            execution = runner.run(
                AgentRunRequest(
                    image=experiment.container_image,
                    digest=experiment.container_digest,
                    workspace=workspace,
                    agent_dir=agent_dir,
                    provider=provider.name,
                    model=model,
                    thinking=judge.thinking,
                    prompt=prompt,
                    no_tools=True,
                    timeout_seconds=experiment.cell_timeout(judge.model_ref),
                    env=env,
                ),
                artifact_dir,
            )

            if isinstance(runner, StubAgentRunner):
                validation: JsonDict = {
                    "valid": True,
                    "parsed": stub_verdict(seed, [m["id"] for m in judge.rubric.metrics]),
                }
            else:
                validation = validate_json_schema_output(execution["chat_history"], VERDICT_SCHEMA)

            invalid = not validation.get("valid", False)
            parsed = validation.get("parsed", {}) if not invalid else {}
            expected_metrics = {m["id"] for m in judge.rubric.metrics}
            if not invalid and set(parsed.get("metrics", {})) != expected_metrics:
                invalid = True

            record: JsonDict = {
                "schema_version": SCHEMA_VERSION,
                "type": "judge_verdict",
                "judge": {
                    "id": judge.id,
                    "model": judge.model_ref,
                    "thinking": judge.thinking,
                    "rubric_version": judge.rubric.version,
                },
                "comparison": {
                    "left": left.cell_id,
                    "right": right.cell_id,
                    "blinding_seed": seed,
                    "presented_order": order,
                },
                "metrics": (
                    {name: unblind(value, order) for name, value in parsed["metrics"].items()}
                    if not invalid
                    else dict.fromkeys(sorted(expected_metrics), "tie")
                ),
                "overall_preference": unblind(parsed.get("overall", "tie"), order)
                if not invalid
                else "tie",
                "rationale": parsed.get("rationale") if not invalid else None,
                "raw_output_ref": str(artifact_dir.relative_to(paths.root)),
                "invalid": invalid,
                "recorded_at": utc_now(),
            }
            validate_against(verdict_schema, record, f"judge verdict {verdict_path.name}")
            verdict_path.write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            records.append(
                {"judge": judge.id, "pair": verdict_path.stem, "status": "invalid" if invalid else "ok"}
            )
    return records


def stub_verdict(seed: int, metric_ids: list[str]) -> JsonDict:
    choices = ["A", "B", "tie"]
    return {
        "metrics": {
            metric: choices[(seed + index) % 3] for index, metric in enumerate(sorted(metric_ids))
        },
        "overall": choices[seed % 3],
        "rationale": "stub verdict",
    }
