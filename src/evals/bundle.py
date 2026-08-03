"""Reproducible cell bundles and their validation.

Ports the tarbomb defense, manifest set-equality, and identity binding from
the v1 validate_bundle.py, generalized to cell_id + spec_hash identity. Local
bundles are unattested; a hosted replay attests the identical bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import pathlib
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from .spec import RunPaths

JsonDict = dict[str, Any]


class BundleError(RuntimeError):
    """Raised when a bundle fails validation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_files(cell_dir: Path) -> list[Path]:
    manifest = cell_dir / "manifest.sha256"
    if not manifest.is_file():
        # A run interrupted between result.json and manifest leaves a
        # manifest-less cell; the manifest is derived state, so regenerate it.
        from .collect import write_manifest

        write_manifest(cell_dir)
    files = [manifest]
    for line in manifest.read_text(encoding="utf-8").splitlines():
        _checksum, separator, name = line.partition("  ")
        if not separator:
            raise BundleError(f"invalid manifest line in {manifest}: {line}")
        files.append(cell_dir / name)
    return files


def write_bundle(cell_dir: Path, destination: Path) -> str:
    """Write a byte-reproducible tar.gz of the cell's manifested artifacts."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(bundle_files(cell_dir), key=lambda p: str(p.relative_to(cell_dir))):
            info = tarfile.TarInfo(name=str(path.relative_to(cell_dir)))
            data = path.read_bytes()
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    with destination.open("wb") as stream:
        with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    return sha256_file(destination)


def run_bundle(paths: RunPaths, experiment_path: Path) -> JsonDict:
    bundles_dir = paths.root / "bundles"
    entries: dict[str, str] = {}
    for cell_dir in sorted(paths.cells.glob("*")):
        if not (cell_dir / "result.json").is_file():
            continue
        digest = write_bundle(cell_dir, bundles_dir / f"{cell_dir.name}.tar.gz")
        entries[cell_dir.name] = digest

    if not entries:
        raise BundleError(f"no completed cells under {paths.cells}")

    run_manifest = {
        "experiment_file_sha256": sha256_file(experiment_path),
        "bundles": entries,
    }
    (bundles_dir / "run.json").write_text(
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_manifest


def validate_bundle(bundle_path: Path, expected_cell_id: str) -> JsonDict:
    with tempfile.TemporaryDirectory(prefix="evals-bundle-") as raw_temp:
        destination = Path(raw_temp)
        with tarfile.open(bundle_path, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = pathlib.PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise BundleError(f"unsafe archive member: {member.name}")
            archive.extractall(destination, filter="data")

        manifest = destination / "manifest.sha256"
        if not manifest.is_file():
            raise BundleError("bundle is missing manifest.sha256")
        expected: dict[Path, str] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            checksum, separator, name = line.partition("  ")
            if not separator or len(checksum) != 64:
                raise BundleError(f"invalid manifest line: {line}")
            member_path = pathlib.PurePosixPath(name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise BundleError(f"unsafe manifest path: {name}")
            expected[destination / name] = checksum

        actual = {path for path in destination.rglob("*") if path.is_file() and path != manifest}
        if set(expected) != actual:
            missing = sorted(str(p.relative_to(destination)) for p in set(expected) - actual)
            unlisted = sorted(str(p.relative_to(destination)) for p in actual - set(expected))
            raise BundleError(f"manifest mismatch; missing={missing}, unlisted={unlisted}")
        for path, checksum in expected.items():
            if sha256_file(path) != checksum:
                raise BundleError(f"digest mismatch: {path.relative_to(destination)}")

        result = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        predicate = json.loads(
            (destination / "attestation-predicate.json").read_text(encoding="utf-8")
        )
        checks = {
            "cell_id": (result.get("cell_id"), expected_cell_id),
            "predicate_cell": (predicate.get("cellId"), expected_cell_id),
            "spec_hash": (result.get("spec_hash"), predicate.get("specHash")),
            "status": (result.get("outcome", {}).get("status"), predicate.get("resultStatus")),
            "model": (
                result.get("variant", {}).get("requested_model"),
                predicate.get("requestedModel"),
            ),
            "provider": (result.get("variant", {}).get("provider"), predicate.get("provider")),
            "source_sha": (result.get("run", {}).get("source_sha"), predicate.get("sourceSha")),
            "container_digest": (
                result.get("container", {}).get("digest"),
                predicate.get("containerDigest"),
            ),
        }
        failures = {name: values for name, values in checks.items() if values[0] != values[1]}
        if failures:
            raise BundleError(f"bundle identity mismatch: {failures}")
        if not result.get("spec_hash"):
            raise BundleError("bundle result lacks spec_hash")

    return {"ok": True, "cell_id": expected_cell_id, "bundle_sha256": sha256_file(bundle_path)}


def run_validate(paths: RunPaths) -> list[JsonDict]:
    bundles_dir = paths.root / "bundles"
    run_manifest_path = bundles_dir / "run.json"
    if not run_manifest_path.is_file():
        raise BundleError(f"no run manifest at {run_manifest_path}; run `evals bundle` first")
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    reports = []
    for cell_id, recorded_digest in sorted(run_manifest["bundles"].items()):
        bundle_path = bundles_dir / f"{cell_id}.tar.gz"
        if sha256_file(bundle_path) != recorded_digest:
            raise BundleError(f"bundle digest drifted since run.json was written: {cell_id}")
        reports.append(validate_bundle(bundle_path, cell_id))
    return reports


def clean_run(paths: RunPaths) -> None:
    shutil.rmtree(paths.root, ignore_errors=True)
