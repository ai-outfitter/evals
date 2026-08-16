"""The contract schemas have to survive being installed as a wheel.

`score-classifier` is the one command a third party runs against its own
results, from its own directory, with `evals` installed rather than checked
out. These tests pin the three things that make this possible: the packaged copy
matches the canonical root schemas, the wheel actually carries them, and an
installed distribution scores a result from an unrelated working directory.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

REPO = pathlib.Path(__file__).resolve().parents[1]
CANONICAL_SCHEMAS = REPO / "schemas"
PACKAGED_SCHEMAS = REPO / "src" / "evals" / "schemas"

VALID_RESULT = {
    "schema_version": 1,
    "kind": "boolean",
    "decision": True,
    "confidence": 0.9,
    "evidence_refs": ["event-log:17"],
}
EXPECTED_LABEL = {"schema_version": 1, "kind": "boolean", "label": True}


def can_build_wheels() -> bool:
    """Whether this interpreter has the declared build tooling (the dev shell)."""
    try:
        import pip  # noqa: F401
        import setuptools  # noqa: F401
        import wheel  # noqa: F401
    except ImportError:
        return False
    return True


class PackagedSchemaCopyTests(unittest.TestCase):
    """`src/evals/schemas/` is a copy, so it needs a guard against drift."""

    def test_the_packaged_copy_holds_exactly_the_canonical_schemas(self):
        canonical = {path.name for path in CANONICAL_SCHEMAS.glob("*.json")}
        packaged = {path.name for path in PACKAGED_SCHEMAS.glob("*.json")}
        self.assertTrue(canonical, "no canonical schemas found")
        self.assertEqual(
            canonical,
            packaged,
            "copy schemas/*.json into src/evals/schemas/ so the wheel ships them",
        )

    def test_every_packaged_schema_is_byte_identical_to_its_canonical_copy(self):
        for path in sorted(CANONICAL_SCHEMAS.glob("*.json")):
            with self.subTest(schema=path.name):
                self.assertEqual(
                    (PACKAGED_SCHEMAS / path.name).read_bytes(),
                    path.read_bytes(),
                    f"src/evals/schemas/{path.name} has drifted from schemas/{path.name}",
                )

    def test_lookup_prefers_the_packaged_copy_over_the_checkout(self):
        from evals import resources

        resolved = resources.schema_path("classifier-result.schema.json").resolve()
        self.assertEqual(resolved.parent, PACKAGED_SCHEMAS.resolve())


@unittest.skipUnless(can_build_wheels(), "needs pip, setuptools, and wheel (nix develop)")
class InstalledDistributionTests(unittest.TestCase):
    """Build a wheel, install it away from the checkout, and score with it."""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls._directory.name)
        # Build from a copy so the build backend leaves no artifacts in the
        # checkout; the copy carries only what the wheel is declared to need.
        source = root / "source"
        source.mkdir()
        shutil.copytree(REPO / "src", source / "src")
        shutil.copy2(REPO / "pyproject.toml", source / "pyproject.toml")

        cls.wheel_dir = root / "wheels"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-index",
                "--no-build-isolation",
                "--wheel-dir",
                str(cls.wheel_dir),
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        wheels = sorted(cls.wheel_dir.glob("evals-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one wheel, got {wheels}")
        cls.wheel = wheels[0]

        cls.target = root / "target"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--target",
                str(cls.target),
                str(cls.wheel),
            ],
            check=True,
            capture_output=True,
        )

        cls.elsewhere = root / "elsewhere"
        cls.elsewhere.mkdir()
        (cls.elsewhere / "result.json").write_text(json.dumps(VALID_RESULT), encoding="utf-8")
        (cls.elsewhere / "expected.json").write_text(json.dumps(EXPECTED_LABEL), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def installed(self, *arguments: str) -> subprocess.CompletedProcess:
        """Run the installed package from a directory unrelated to the checkout.

        PYTHONPATH is replaced rather than extended: check.sh exports the
        checkout's `src/`, and inheriting it would let the test pass on the
        source tree it is meant to exclude.
        """
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=self.elsewhere,
            env={**os.environ, "PYTHONPATH": str(self.target)},
            capture_output=True,
            text=True,
        )

    def test_the_wheel_carries_every_contract_schema(self):
        with zipfile.ZipFile(self.wheel) as archive:
            names = set(archive.namelist())
        for path in sorted(CANONICAL_SCHEMAS.glob("*.json")):
            with self.subTest(schema=path.name):
                self.assertIn(f"evals/schemas/{path.name}", names)

    def test_the_installed_package_is_the_one_under_test(self):
        completed = self.installed("-c", "import evals; print(evals.__file__)")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        imported = pathlib.Path(completed.stdout.strip()).resolve()
        self.assertTrue(
            imported.is_relative_to(self.target.resolve()),
            f"imported {imported}, not the installed copy under {self.target}",
        )

    def test_score_classifier_runs_from_an_unrelated_working_directory(self):
        completed = self.installed(
            "-m", "evals.cli", "score-classifier", "result.json", "expected.json"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        printed = json.loads(completed.stdout)
        self.assertTrue(printed["pass"])
        self.assertEqual(printed["kind"], "boolean")
        self.assertEqual(printed["scores"]["accuracy"]["value"], 1.0)

    def test_the_installed_schemas_come_from_the_installed_package(self):
        completed = self.installed(
            "-c",
            "from evals.resources import schema_path; "
            "print(schema_path('classifier-result.schema.json'))",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        resolved = pathlib.Path(completed.stdout.strip()).resolve()
        self.assertEqual(resolved.parent, (self.target / "evals" / "schemas").resolve())


if __name__ == "__main__":
    unittest.main()
