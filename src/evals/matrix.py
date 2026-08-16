"""Matrix expansion, cell identity, and resume logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .pi import slugify
from .spec import Experiment, SpecError

JsonDict = dict[str, Any]


@dataclass(slots=True)
class Cell:
    experiment_id: str
    task_id: str
    model_ref: str
    provider: str
    model: str
    thinking: str
    agent: str
    repetition: int
    timeout_seconds: float

    @property
    def cell_id(self) -> str:
        return "-".join(
            [
                slugify(self.task_id),
                slugify(self.model_ref),
                slugify(self.thinking),
                slugify(self.agent),
                f"r{self.repetition}",
            ]
        )


def spec_hash(experiment: Experiment, cell: Cell) -> str:
    def file_digest(path: Any) -> str | None:
        if path is None:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    resolved = {
        "experiment_id": experiment.id,
        "task_id": experiment.task.id,
        "task_kind": experiment.task.kind,
        "prompt_sha256": hashlib.sha256(experiment.task.prompt.encode()).hexdigest(),
        "input_sha256": file_digest(experiment.task.input),
        "expected_sha256": file_digest(experiment.task.expected),
        "agent_instruction_sha256": hashlib.sha256(
            experiment.agent_instructions.get(cell.agent, "").encode()
        ).hexdigest(),
        "container_image": experiment.container_image,
        "container_digest": experiment.container_digest,
        "model_ref": cell.model_ref,
        "thinking": cell.thinking,
        "agent": cell.agent,
        "repetition": cell.repetition,
        "timeout_seconds": cell.timeout_seconds,
    }
    canonical = json.dumps(resolved, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def expand_matrix(experiment: Experiment) -> list[Cell]:
    matrix = experiment.matrix
    repetitions = int(matrix.get("repetitions", 1))
    cells: list[Cell] = []
    for model_ref in matrix["model"]:
        model_ref = str(model_ref)
        provider, model = experiment.registry.resolve(model_ref)
        for thinking in matrix["thinking"]:
            for agent in matrix["agent"]:
                for repetition in range(1, repetitions + 1):
                    cells.append(
                        Cell(
                            experiment_id=experiment.id,
                            task_id=experiment.task.id,
                            model_ref=model_ref,
                            provider=provider.name,
                            model=model,
                            thinking=str(thinking),
                            agent=str(agent),
                            repetition=repetition,
                            timeout_seconds=experiment.cell_timeout(model_ref),
                        )
                    )
    return cells


def parse_filters(raw_filters: list[str]) -> dict[str, set[str]]:
    filters: dict[str, set[str]] = {}
    for raw in raw_filters:
        key, separator, values = raw.partition("=")
        if not separator or key not in {"model", "thinking", "agent"}:
            raise SpecError(f"invalid filter {raw!r}; expected model=…, thinking=…, or agent=…")
        filters.setdefault(key, set()).update(v for v in values.split(",") if v)
    return filters


def apply_filters(cells: list[Cell], filters: dict[str, set[str]]) -> list[Cell]:
    def keep(cell: Cell) -> bool:
        if "model" in filters and cell.model_ref not in filters["model"] and cell.model not in filters["model"]:
            return False
        if "thinking" in filters and cell.thinking not in filters["thinking"]:
            return False
        if "agent" in filters and cell.agent not in filters["agent"]:
            return False
        return True

    return [cell for cell in cells if keep(cell)]


def comparison_pairs(
    cells: list[Cell], independent_variables: list[str]
) -> list[tuple[Cell, Cell]]:
    """Pairs of cells that differ in exactly one independent variable.

    Repetition and all other axes must match, so every pair isolates one
    variable — the AGENTS.md single-independent-variable discipline.
    """

    def axes(cell: Cell) -> JsonDict:
        return {"model": cell.model_ref, "thinking": cell.thinking, "agent": cell.agent}

    pairs: list[tuple[Cell, Cell]] = []
    for index, left in enumerate(cells):
        for right in cells[index + 1 :]:
            if left.repetition != right.repetition:
                continue
            left_axes, right_axes = axes(left), axes(right)
            differing = [
                axis for axis in independent_variables if left_axes[axis] != right_axes[axis]
            ]
            same_elsewhere = all(
                left_axes[axis] == right_axes[axis]
                for axis in left_axes
                if axis not in differing
            )
            if len(differing) == 1 and same_elsewhere:
                pairs.append((left, right))
    return pairs
