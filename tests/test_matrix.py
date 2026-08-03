import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from evals.matrix import apply_filters, comparison_pairs, expand_matrix, parse_filters, spec_hash
from evals.spec import load_experiment

REPO = pathlib.Path(__file__).resolve().parents[1]
EXP_001 = REPO / "experiments" / "exp-001-tasksmd-thinking" / "experiment.yaml"
EXP_002 = REPO / "experiments" / "exp-002-transcript-summary" / "experiment.yaml"


class MatrixTests(unittest.TestCase):
    def test_expansion_counts(self):
        experiment = load_experiment(EXP_001)
        cells = expand_matrix(experiment)
        self.assertEqual(len(cells), 9)  # 3 models x 3 thinking x 1 agent x 1 rep

    def test_cell_ids_unique_and_slugged(self):
        experiment = load_experiment(EXP_002)
        cells = expand_matrix(experiment)
        ids = [cell.cell_id for cell in cells]
        self.assertEqual(len(ids), len(set(ids)))
        for cell_id in ids:
            self.assertRegex(cell_id, r"^[a-z0-9-]+-r\d+$")

    def test_spec_hash_stable_and_distinct(self):
        experiment = load_experiment(EXP_001)
        cells = expand_matrix(experiment)
        hashes = {spec_hash(experiment, cell) for cell in cells}
        self.assertEqual(len(hashes), len(cells))
        self.assertEqual(spec_hash(experiment, cells[0]), spec_hash(experiment, cells[0]))

    def test_filters(self):
        experiment = load_experiment(EXP_001)
        cells = expand_matrix(experiment)
        filtered = apply_filters(cells, parse_filters(["model=ollama/qwen3:4b", "thinking=off,low"]))
        self.assertEqual(len(filtered), 2)
        for cell in filtered:
            self.assertEqual(cell.model_ref, "ollama/qwen3:4b")
            self.assertIn(cell.thinking, {"off", "low"})

    def test_thinking_off_stays_string(self):
        experiment = load_experiment(EXP_001)
        thinking_values = {cell.thinking for cell in expand_matrix(experiment)}
        self.assertIn("off", thinking_values)
        self.assertNotIn("False", thinking_values)

    def test_comparison_pairs_differ_in_one_axis(self):
        experiment = load_experiment(EXP_001)
        cells = expand_matrix(experiment)
        pairs = comparison_pairs(cells, experiment.independent_variables)
        self.assertTrue(pairs)
        for left, right in pairs:
            differing = sum(
                1
                for axis in ("model_ref", "thinking", "agent")
                if getattr(left, axis) != getattr(right, axis)
            )
            self.assertEqual(differing, 1)


if __name__ == "__main__":
    unittest.main()
