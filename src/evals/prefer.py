"""Interactive, resumable human preference capture over blinded pairs."""

from __future__ import annotations

import json
from typing import Any

from . import SCHEMA_VERSION
from .judge import blinding_seed, judgeable_cells, load_side_inputs, presented_order, unblind
from .matrix import Cell, comparison_pairs
from .pi import utc_now
from .spec import Experiment, RunPaths, load_schema, validate_against

JsonDict = dict[str, Any]

VALID_ANSWERS = {"a": "A", "b": "B", "t": "tie", "tie": "tie", "A": "A", "B": "B"}


def show_pair(label_inputs: dict[str, dict[str, str]], metrics: list[str]) -> None:
    for label in ("A", "B"):
        print(f"\n{'=' * 30} RESPONSE {label} {'=' * 30}")
        for input_kind, text in label_inputs[label].items():
            body = text.strip() or "(empty)"
            if len(body) > 6000:
                body = body[:6000] + "\n[... truncated; open the cell dir for the rest ...]"
            print(f"\n--- {input_kind} ---\n{body}")
    print(f"\nMetrics to rate: {', '.join(metrics)}")


def ask(prompt: str, *, auto: str | None) -> str:
    if auto is not None:
        return auto
    while True:
        answer = input(prompt).strip()
        if answer in VALID_ANSWERS:
            return VALID_ANSWERS[answer]
        print("answer a, b, or t(ie)")


def run_preferences(
    experiment: Experiment,
    paths: RunPaths,
    cells: list[Cell],
    *,
    run_id: str,
    rater: str,
    auto: bool = False,
) -> list[JsonDict]:
    usable = judgeable_cells(experiment, paths, cells)
    pairs = comparison_pairs(usable, experiment.independent_variables)
    schema = load_schema("preference.schema.json")
    paths.preferences.mkdir(parents=True, exist_ok=True)
    metrics = experiment.human_metrics
    records: list[JsonDict] = []

    pending = []
    for left, right in pairs:
        record_path = paths.preferences / f"{left.cell_id}__{right.cell_id}.json"
        if record_path.exists():
            records.append({"pair": record_path.stem, "status": "skipped"})
            continue
        pending.append((left, right, record_path))

    if not pending:
        print("nothing pending")
        return records

    for index, (left, right, record_path) in enumerate(pending, start=1):
        seed = blinding_seed(run_id, f"human:{rater}", left.cell_id, right.cell_id)
        order = presented_order(seed)
        by_side = {
            "left": load_side_inputs(experiment, paths, left),
            "right": load_side_inputs(experiment, paths, right),
        }
        label_inputs = {"A": by_side[order[0]], "B": by_side[order[1]]}

        if not auto:
            print(f"\n############ pair {index}/{len(pending)} ############")
            show_pair(label_inputs, metrics)

        metric_answers: dict[str, str] = {}
        for metric in metrics:
            metric_answers[metric] = ask(
                f"{metric} — better response [a/b/t]: ", auto="tie" if auto else None
            )
        overall = ask("overall — better response [a/b/t]: ", auto="tie" if auto else None)

        record: JsonDict = {
            "schema_version": SCHEMA_VERSION,
            "type": "human_preference",
            "rater": {"id": rater},
            "comparison": {
                "left": left.cell_id,
                "right": right.cell_id,
                "blinding_seed": seed,
                "presented_order": order,
            },
            "metrics": {name: unblind(value, order) for name, value in metric_answers.items()},
            "overall_preference": unblind(overall, order),
            "recorded_at": utc_now(),
        }
        validate_against(schema, record, f"preference {record_path.name}")
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        records.append({"pair": record_path.stem, "status": "ok"})
    return records
