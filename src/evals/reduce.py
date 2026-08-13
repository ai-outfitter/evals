"""Aggregate cell results, judge verdicts, and human preferences into a report."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .spec import Experiment, RunPaths

JsonDict = dict[str, Any]

AXES = ("model", "thinking", "agent")


def load_records(directory: Path) -> list[JsonDict]:
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.rglob("*.json")):
        if path.name == "models.json" or path.parent.name.endswith(".artifacts"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") in {"judge_verdict", "human_preference"}:
            records.append(payload)
    return records


def load_results(paths: RunPaths) -> dict[str, JsonDict]:
    results: dict[str, JsonDict] = {}
    if not paths.cells.exists():
        return results
    for result_path in sorted(paths.cells.glob("*/result.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        results[result["cell_id"]] = result
    return results


def cell_axes(result: JsonDict) -> dict[str, str]:
    variant = result["variant"]
    return {
        "model": f"{variant['provider']}/{variant['requested_model']}",
        "thinking": variant["thinking"],
        "agent": variant["agent"],
    }


def tally_pairwise(
    records: list[JsonDict], results: dict[str, JsonDict]
) -> JsonDict:
    """wins[axis][value][metric] = {win, loss, tie} counted over pairs differing on that axis."""
    wins: JsonDict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"win": 0, "loss": 0, "tie": 0})))
    for record in records:
        left_id = record["comparison"]["left"]
        right_id = record["comparison"]["right"]
        left = results.get(left_id)
        right = results.get(right_id)
        if left is None or right is None:
            continue
        left_axes, right_axes = cell_axes(left), cell_axes(right)
        differing = [axis for axis in AXES if left_axes[axis] != right_axes[axis]]
        if len(differing) != 1:
            continue
        axis = differing[0]
        judgments = dict(record["metrics"])
        judgments["overall"] = record["overall_preference"]
        for metric, verdict in judgments.items():
            if verdict == "tie":
                wins[axis][left_axes[axis]][metric]["tie"] += 1
                wins[axis][right_axes[axis]][metric]["tie"] += 1
            elif verdict == "left":
                wins[axis][left_axes[axis]][metric]["win"] += 1
                wins[axis][right_axes[axis]][metric]["loss"] += 1
            elif verdict == "right":
                wins[axis][right_axes[axis]][metric]["win"] += 1
                wins[axis][left_axes[axis]][metric]["loss"] += 1
    return json.loads(json.dumps(wins))


def agreement(
    verdicts: list[JsonDict], preferences: list[JsonDict]
) -> JsonDict:
    """Per judge: fraction of shared (pair, metric) verdicts matching the human's."""
    human: dict[tuple[str, str, str], str] = {}
    for record in preferences:
        key_base = (record["comparison"]["left"], record["comparison"]["right"])
        for metric, verdict in record["metrics"].items():
            human[(*key_base, metric)] = verdict
        human[(*key_base, "overall")] = record["overall_preference"]

    per_judge: JsonDict = {}
    for record in verdicts:
        judge_id = record["judge"]["id"]
        stats = per_judge.setdefault(judge_id, {"shared": 0, "matching": 0})
        key_base = (record["comparison"]["left"], record["comparison"]["right"])
        judgments = dict(record["metrics"])
        judgments["overall"] = record["overall_preference"]
        for metric, verdict in judgments.items():
            human_verdict = human.get((*key_base, metric))
            if human_verdict is None:
                continue
            stats["shared"] += 1
            if human_verdict == verdict:
                stats["matching"] += 1
    for stats in per_judge.values():
        stats["agreement"] = round(stats["matching"] / stats["shared"], 3) if stats["shared"] else None
    return per_judge


def win_rate(counts: JsonDict) -> str:
    decided = counts["win"] + counts["loss"]
    if decided == 0:
        return "—"
    return f"{counts['win']}/{decided} ({counts['win'] / decided:.0%})"


def markdown_report(
    experiment: Experiment,
    results: dict[str, JsonDict],
    judge_wins: JsonDict,
    human_wins: JsonDict,
    judge_agreement: JsonDict,
) -> str:
    lines = [
        f"# {experiment.id}",
        "",
        f"> {experiment.document['question'].strip()}",
        "",
        "## Cells",
        "",
        "| cell | status | failure class | scores | wall clock (s) | tokens out |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for cell_id, result in sorted(results.items()):
        scores = ", ".join(
            f"{name}={score['value']}/{score['max']}"
            for name, score in sorted(result["outcome"]["scores"].items())
        ) or "—"
        session = result["execution"]["session"]
        lines.append(
            f"| {cell_id} | {result['outcome']['status']} | {result['outcome']['failure_class']} "
            f"| {scores} | {session.get('wall_clock_seconds', 0):.0f} "
            f"| {session.get('usage', {}).get('output', 0)} |"
        )

    for title, wins in (("Judge preferences", judge_wins), ("Human preferences", human_wins)):
        lines.extend(["", f"## {title}", ""])
        if not wins:
            lines.append("(no records)")
            continue
        for axis, values in sorted(wins.items()):
            metrics = sorted({metric for counts in values.values() for metric in counts})
            lines.extend(
                [
                    f"### by {axis}",
                    "",
                    "| " + axis + " | " + " | ".join(metrics) + " |",
                    "| --- |" + " --- |" * len(metrics),
                ]
            )
            for value, counts in sorted(values.items()):
                row = [value] + [
                    win_rate(counts.get(metric, {"win": 0, "loss": 0, "tie": 0}))
                    for metric in metrics
                ]
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

    lines.extend(["", "## Judge vs human agreement", ""])
    if judge_agreement:
        lines.append("| judge | shared verdicts | matching | agreement |")
        lines.append("| --- | --- | --- | --- |")
        for judge_id, stats in sorted(judge_agreement.items()):
            rate = f"{stats['agreement']:.0%}" if stats["agreement"] is not None else "—"
            lines.append(f"| {judge_id} | {stats['shared']} | {stats['matching']} | {rate} |")
    else:
        lines.append("(no overlapping records)")
    lines.append("")
    return "\n".join(lines)


def run_reduce(experiment: Experiment, paths: RunPaths) -> JsonDict:
    results = load_results(paths)
    verdicts = [r for r in load_records(paths.judgments) if not r.get("invalid")]
    preferences = load_records(paths.preferences)

    judge_wins = tally_pairwise(verdicts, results)
    human_wins = tally_pairwise(preferences, results)
    judge_agreement = agreement(verdicts, preferences)

    summary = {
        "experiment_id": experiment.id,
        "cells": {
            cell_id: {
                "status": result["outcome"]["status"],
                "failure_class": result["outcome"]["failure_class"],
                "scores": result["outcome"]["scores"],
            }
            for cell_id, result in sorted(results.items())
        },
        "judge_verdicts": len(verdicts),
        "human_preferences": len(preferences),
        "judge_wins": judge_wins,
        "human_wins": human_wins,
        "judge_agreement": judge_agreement,
    }
    (paths.root / "results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (paths.root / "report.md").write_text(
        markdown_report(experiment, results, judge_wins, human_wins, judge_agreement),
        encoding="utf-8",
    )
    return summary
