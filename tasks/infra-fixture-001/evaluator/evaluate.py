#!/usr/bin/env python3
"""Evaluator kept outside the agent worktree for the infrastructure fixture."""

from __future__ import annotations

import argparse
import importlib.util
import math
import pathlib
import sys
import unittest


def load_subject(repo: pathlib.Path):
    path = repo / "calculator.py"
    spec = importlib.util.spec_from_file_location("calculator_subject", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_and_sum


def suite_for(function):
    class EvaluatorTests(unittest.TestCase):
        def test_mixed_values(self):
            self.assertTrue(math.isclose(function([1, " 2.5 ", 3.25]), 6.75))

        def test_input_is_not_mutated(self):
            values = [" 2 ", 3]
            function(values)
            self.assertEqual(values, [" 2 ", 3])

        def test_boolean_is_rejected(self):
            with self.assertRaises(ValueError):
                function([True])

        def test_blank_is_rejected(self):
            with self.assertRaises(ValueError):
                function(["   "])

        def test_unsupported_value_is_rejected(self):
            with self.assertRaises(ValueError):
                function([object()])

    return unittest.defaultTestLoader.loadTestsFromTestCase(EvaluatorTests)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    args = parser.parse_args()
    function = load_subject(args.repo.resolve())
    result = unittest.TextTestRunner(verbosity=2).run(suite_for(function))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
