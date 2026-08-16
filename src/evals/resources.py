"""Locate the JSON Schemas the harness validates against.

The canonical copies live in the repository's `schemas/` directory, which
`Path(__file__)` alone stops finding once `evals` is installed as a wheel
somewhere else. The wheel therefore ships `evals/schemas/` (declared as
package data in pyproject.toml, kept byte-identical to the root copies by
tests/test_packaging.py), and lookup asks the installed distribution first
before walking up from this file and from the working directory. That order is
what lets an installed `evals score-classifier` run from any directory.

A schema that cannot be found or read is the harness's own fault, never a
candidate's: that distinction is why lookup raises HarnessError rather than a
scoring error.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]

SCHEMA_DIR_NAME = "schemas"


class HarnessError(RuntimeError):
    """Raised when the harness itself is misinstalled or missing its own files."""


def packaged_schema_dir() -> Path | None:
    """The schemas shipped inside the installed `evals` package, if readable."""
    try:
        directory = Path(str(resources.files(__package__) / SCHEMA_DIR_NAME))
    except (ModuleNotFoundError, TypeError, ValueError):
        return None
    return directory if directory.is_dir() else None


def schema_dirs() -> list[Path]:
    """Every directory that could hold the schemas, most specific first."""
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    directories: list[Path] = []
    packaged = packaged_schema_dir()
    if packaged is not None:
        directories.append(packaged)
    roots = [here.parent, *here.parents, cwd, *cwd.parents]
    for root in roots:
        candidate = root / SCHEMA_DIR_NAME
        if candidate not in directories:
            directories.append(candidate)
    return directories


def schema_path(name: str) -> Path:
    for directory in schema_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise HarnessError(
        f"cannot locate schema {name}: no {SCHEMA_DIR_NAME}/ directory above "
        f"{Path(__file__).resolve().parent} or {Path.cwd().resolve()}"
    )


def read_schema(name: str) -> JsonDict:
    path = schema_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"cannot read schema {path}: {error}") from error
