#!/usr/bin/env python3
"""Held-out evaluator for the TASKS.md calculator fixture.

Emits a JSON summary on stdout:
{"scores": {"tasks_completed": {"value": n, "max": 5},
            "tests_pass": {"value": 0|1, "max": 1}}, "pass": bool}
Exit code 0 means the evaluator itself ran; task failures live in the scores.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys


def load_module(repo: pathlib.Path):
    path = repo / "calculator.py"
    spec = importlib.util.spec_from_file_location("calculator_subject", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(predicate) -> bool:
    try:
        return bool(predicate())
    except Exception:
        return False


def raises_value_error(function, *args) -> bool:
    try:
        function(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    args = parser.parse_args()
    repo = args.repo.resolve()

    try:
        module = load_module(repo)
    except Exception as error:  # broken module: every task scores zero
        print(json.dumps({
            "scores": {
                "tasks_completed": {"value": 0, "max": 5},
                "tests_pass": {"value": 0, "max": 1},
            },
            "pass": False,
            "error": f"import failed: {error}",
        }))
        return 0

    task_subtract = check(lambda: module.subtract(5, 3) == 2 and module.subtract(3, 5) == -2)
    task_multiply = check(lambda: module.multiply(4, 3) == 12 and module.multiply(-2, 5) == -10)
    task_divide = check(lambda: module.divide(10, 4) == 2.5) and raises_value_error(
        module.divide, 1, 0
    )
    task_validation = all(
        raises_value_error(operation, True, 1) and raises_value_error(operation, 1, False)
        for operation in (module.add, module.subtract, module.multiply, module.divide)
    )
    readme = (repo / "README.md").read_text(encoding="utf-8", errors="replace") if (repo / "README.md").exists() else ""
    task_readme = "## Usage" in readme and "divide" in readme

    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    tests_pass = tests.returncode == 0

    completed = sum(
        [task_subtract, task_multiply, task_divide, task_validation, task_readme]
    )
    summary = {
        "scores": {
            "tasks_completed": {"value": completed, "max": 5},
            "tests_pass": {"value": 1 if tests_pass else 0, "max": 1},
        },
        "pass": completed == 5 and tests_pass,
        "tasks": {
            "fix_subtract": task_subtract,
            "implement_multiply": task_multiply,
            "implement_divide": task_divide,
            "boolean_validation": task_validation,
            "readme_usage": task_readme,
        },
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
