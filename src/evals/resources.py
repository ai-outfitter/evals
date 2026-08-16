"""Locate the JSON Schemas the harness validates against.

The canonical copies live in the repository's `schemas/` directory, which
`Path(__file__)` alone stops finding once `evals` is installed as a wheel
somewhere else. The wheel therefore ships `evals/schemas/` (declared as package
data in pyproject.toml, kept byte-identical to the root copies by
tests/test_packaging.py), and lookup reads that packaged copy and nothing else.
Depending on neither a checkout nor the working directory is what lets an
installed `evals score-classifier` run from any directory.

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


def schema_path(name: str) -> Path:
    """The packaged schema of that name, or HarnessError if it is not shipped."""
    try:
        candidate = Path(str(resources.files(__package__) / SCHEMA_DIR_NAME / name))
    except (ModuleNotFoundError, TypeError, ValueError) as error:
        raise HarnessError(f"cannot locate schema {name}: {error}") from error
    if not candidate.is_file():
        raise HarnessError(
            f"cannot locate schema {name}: the installed evals package does not "
            f"ship {SCHEMA_DIR_NAME}/{name}"
        )
    return candidate


def read_schema(name: str) -> JsonDict:
    path = schema_path(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"cannot read schema {path}: {error}") from error
