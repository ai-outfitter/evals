"""Load and validate experiment, task, rubric, and provider specs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for

from .pi import load_yaml

JsonDict = dict[str, Any]


class SpecError(RuntimeError):
    """Raised when a spec file is missing, malformed, or invalid."""


def find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "schemas" / "experiment.schema.json").is_file():
            return candidate
    raise SpecError(f"could not locate repo root (schemas/) above {start}")


def load_schema(repo_root: Path, name: str) -> JsonDict:
    return json.loads((repo_root / "schemas" / name).read_text(encoding="utf-8"))


def validate_against(schema: JsonDict, document: Any, label: str) -> None:
    validator = validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise SpecError(f"{label}: {path}: {first.message}")


def load_yaml_file(path: Path, label: str) -> JsonDict:
    if not path.is_file():
        raise SpecError(f"{label} not found: {path}")
    document = load_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SpecError(f"{label} must contain a top-level mapping: {path}")
    return document


@dataclass(slots=True)
class Provider:
    name: str
    api: str
    base_url: str
    base_url_container: str | None
    api_key_env: str | None
    api_key_literal: str | None
    compat: JsonDict
    models: list[JsonDict]
    runner: str = "pi-container"

    def pi_provider_block(self, *, in_container: bool) -> JsonDict:
        base_url = self.base_url
        if in_container and self.base_url_container:
            base_url = self.base_url_container
        block: JsonDict = {
            "baseUrl": base_url,
            "api": self.api,
            "compat": self.compat,
            "models": [
                {
                    "id": model["id"],
                    "name": model.get("name", model["id"]),
                    "reasoning": bool(model.get("reasoning", False)),
                    "input": model.get("input", ["text"]),
                    "contextWindow": int(model.get("context_window", 65536)),
                    "maxTokens": int(model.get("max_tokens", 8192)),
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                }
                for model in self.models
            ],
        }
        if self.api_key_env:
            block["apiKey"] = f"${self.api_key_env}"
            block["authHeader"] = True
        elif self.api_key_literal:
            block["apiKey"] = self.api_key_literal
        return block


@dataclass(slots=True)
class ProviderRegistry:
    providers: dict[str, Provider]

    def resolve(self, model_ref: str) -> tuple[Provider, str]:
        provider_name, separator, model_id = model_ref.partition("/")
        if not separator:
            raise SpecError(f"model reference must be provider/model: {model_ref}")
        provider = self.providers.get(provider_name)
        if provider is None:
            raise SpecError(f"unknown provider {provider_name!r} in {model_ref}")
        if model_id not in {model["id"] for model in provider.models}:
            raise SpecError(f"model {model_id!r} is not registered under provider {provider_name!r}")
        return provider, model_id

    def pi_models_json(self, *, in_container: bool) -> JsonDict:
        return {
            "providers": {
                name: provider.pi_provider_block(in_container=in_container)
                for name, provider in self.providers.items()
                if provider.runner == "pi-container"
            }
        }


def load_provider_registry(path: Path) -> ProviderRegistry:
    document = load_yaml_file(path, "provider registry")
    raw_providers = document.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise SpecError(f"provider registry must define a non-empty providers mapping: {path}")

    providers: dict[str, Provider] = {}
    for name, raw in raw_providers.items():
        if not isinstance(raw, dict):
            raise SpecError(f"provider {name!r} must be a mapping")
        api_key = raw.get("api_key", {})
        if not isinstance(api_key, dict):
            raise SpecError(f"provider {name!r} api_key must be a mapping with env or literal")
        models = raw.get("models")
        if not isinstance(models, list) or not models:
            raise SpecError(f"provider {name!r} must define a non-empty models list")
        runner = str(raw.get("runner", "pi-container"))
        if runner == "pi-container" and "base_url" not in raw:
            raise SpecError(f"provider {name!r} needs base_url for the pi-container runner")
        providers[name] = Provider(
            name=name,
            api=str(raw.get("api", "openai-completions")),
            base_url=str(raw.get("base_url", "")),
            base_url_container=raw.get("base_url_container"),
            api_key_env=api_key.get("env"),
            api_key_literal=api_key.get("literal"),
            compat=raw.get("compat", {}),
            models=models,
            runner=runner,
        )
    return ProviderRegistry(providers=providers)


@dataclass(slots=True)
class Task:
    path: Path
    id: str
    kind: str
    prompt: str
    metrics: list[str]
    fixture: Path | None
    input: Path | None
    evaluator: Path | None
    evaluator_format: str

    @property
    def root(self) -> Path:
        return self.path.parent


@dataclass(slots=True)
class Rubric:
    path: Path
    version: str
    metrics: list[JsonDict]
    instructions: str


@dataclass(slots=True)
class Judge:
    id: str
    model_ref: str
    thinking: str
    rubric: Rubric


@dataclass(slots=True)
class Experiment:
    path: Path
    repo_root: Path
    document: JsonDict
    registry: ProviderRegistry
    task: Task
    judges: list[Judge]

    @property
    def id(self) -> str:
        return str(self.document["id"])

    @property
    def independent_variables(self) -> list[str]:
        return [str(v) for v in self.document["independent_variables"]]

    @property
    def container_image(self) -> str:
        return str(self.document["container"]["image"])

    @property
    def container_digest(self) -> str | None:
        return self.document["container"].get("digest")

    @property
    def matrix(self) -> JsonDict:
        return self.document["matrix"]

    @property
    def judging_inputs(self) -> list[str]:
        return [str(v) for v in self.document["judging"]["inputs"]]

    @property
    def human_metrics(self) -> list[str]:
        return [str(v) for v in self.document["human"]["metrics"]]

    def cell_timeout(self, model_ref: str) -> float:
        limits = self.document.get("limits", {})
        override = limits.get("per_model_overrides", {}).get(model_ref, {})
        return float(override.get("cell_timeout_seconds", limits.get("cell_timeout_seconds", 1800)))


def load_task(path: Path, repo_root: Path) -> Task:
    document = load_yaml_file(path, "task spec")
    validate_against(load_schema(repo_root, "task.schema.json"), document, f"task spec {path}")

    def resolve(name: str) -> Path | None:
        value = document.get(name)
        if value is None:
            return None
        resolved = (path.parent / str(value)).resolve()
        if not resolved.exists():
            raise SpecError(f"task {document['id']} {name} does not exist: {resolved}")
        return resolved

    return Task(
        path=path,
        id=str(document["id"]),
        kind=str(document["kind"]),
        prompt=str(document["prompt"]),
        metrics=[str(m) for m in document["metrics"]],
        fixture=resolve("fixture"),
        input=resolve("input"),
        evaluator=resolve("evaluator"),
        evaluator_format=str(document.get("evaluator_format", "json")),
    )


def load_rubric(path: Path) -> Rubric:
    document = load_yaml_file(path, "judge rubric")
    metrics = document.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise SpecError(f"rubric must define a non-empty metrics list: {path}")
    for metric in metrics:
        if not isinstance(metric, dict) or "id" not in metric or "description" not in metric:
            raise SpecError(f"rubric metrics need id and description: {path}")
    return Rubric(
        path=path,
        version=str(document.get("version", "1")),
        metrics=metrics,
        instructions=str(document.get("instructions", "")),
    )


def load_experiment(path: Path) -> Experiment:
    path = path.resolve()
    repo_root = find_repo_root(path.parent)
    document = load_yaml_file(path, "experiment spec")
    validate_against(
        load_schema(repo_root, "experiment.schema.json"), document, f"experiment {path}"
    )

    registry = load_provider_registry((path.parent / str(document["providers"])).resolve())
    task = load_task((path.parent / str(document["task"])).resolve(), repo_root)

    if task.kind.startswith("worktree") and task.fixture is None:
        raise SpecError(f"task {task.id}: worktree kinds require a fixture")
    if task.kind.startswith("transform") and task.input is None:
        raise SpecError(f"task {task.id}: transform kinds require an input")

    judges = []
    for raw in document["judges"]:
        judges.append(
            Judge(
                id=str(raw["id"]),
                model_ref=str(raw["model"]),
                thinking=str(raw.get("thinking", "off")),
                rubric=load_rubric((path.parent / str(raw["rubric"])).resolve()),
            )
        )

    experiment = Experiment(
        path=path,
        repo_root=repo_root,
        document=document,
        registry=registry,
        task=task,
        judges=judges,
    )

    for model_ref in experiment.matrix["model"]:
        registry.resolve(str(model_ref))
    for judge in judges:
        registry.resolve(judge.model_ref)
        missing = [m for m in experiment.human_metrics if m not in {r["id"] for r in judge.rubric.metrics}]
        if missing:
            raise SpecError(
                f"judge {judge.id}: rubric {judge.rubric.path.name} lacks metrics {missing}"
            )
    if experiment.document["provenance"]["require_image_digest"] and not experiment.container_digest:
        raise SpecError("provenance.require_image_digest is true but container.digest is null")

    return experiment


@dataclass(slots=True)
class RunPaths:
    root: Path
    cells: Path = field(init=False)
    judgments: Path = field(init=False)
    preferences: Path = field(init=False)

    def __post_init__(self) -> None:
        self.cells = self.root / "cells"
        self.judgments = self.root / "judgments"
        self.preferences = self.root / "preferences"


def run_paths(repo_root: Path, experiment_id: str, run_id: str) -> RunPaths:
    return RunPaths(root=repo_root / "runs" / experiment_id / run_id)
