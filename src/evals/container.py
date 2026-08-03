"""Run Pi inside a container via podman or docker."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .pi import parse_pi_output, utc_now

JsonDict = dict[str, Any]


class ContainerError(RuntimeError):
    """Raised when the container runtime fails before the agent runs."""


def runtime_binary() -> str:
    return os.environ.get("EVALS_CONTAINER_RUNTIME", "podman")


def runtime_version() -> str | None:
    try:
        completed = subprocess.run(
            [runtime_binary(), "--version"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return None
    return completed.stdout.strip() or None


def image_reference(image: str, digest: str | None) -> str:
    return f"{image}@{digest}" if digest else image


def inspect_image(reference: str) -> JsonDict:
    completed = subprocess.run(
        [runtime_binary(), "image", "inspect", reference],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ContainerError(f"image not available: {reference}: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout)
    entry = payload[0] if isinstance(payload, list) else payload
    return entry


def observed_digest(reference: str) -> str:
    """Return the strongest digest available for a local image.

    Pulled images carry RepoDigests; locally built ones only an image Id.
    Both are sha256-prefixed and stable, and both are recorded verbatim.
    """
    entry = inspect_image(reference)
    repo_digests = entry.get("RepoDigests") or []
    for repo_digest in repo_digests:
        _, _, digest = str(repo_digest).partition("@")
        if digest.startswith("sha256:"):
            return digest
    image_id = str(entry.get("Id", ""))
    if image_id and not image_id.startswith("sha256:"):
        image_id = f"sha256:{image_id}"
    if not image_id:
        raise ContainerError(f"image has neither RepoDigests nor Id: {reference}")
    return image_id


def verify_digest(image: str, expected: str | None) -> str:
    reference = image_reference(image, expected)
    digest = observed_digest(reference)
    if expected and digest != expected:
        raise ContainerError(f"image digest mismatch: expected {expected}, observed {digest}")
    return digest


@dataclass(slots=True)
class AgentRunRequest:
    image: str
    digest: str | None
    workspace: Path
    agent_dir: Path
    pi_args: list[str]
    timeout_seconds: float
    env: dict[str, str]


class AgentRunner(Protocol):
    def run(self, request: AgentRunRequest, artifact_dir: Path) -> JsonDict:
        """Execute the agent and return a normalized execution payload."""


class PodmanAgentRunner:
    """Runs `pi` inside the pinned container image."""

    network = "host"

    def run(self, request: AgentRunRequest, artifact_dir: Path) -> JsonDict:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        name = f"evals-{uuid.uuid4().hex[:12]}"
        command = [
            runtime_binary(),
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            self.network,
            "-v",
            f"{request.workspace}:/workspace",
            "-v",
            f"{request.agent_dir}:/pi-agent",
            "-e",
            "PI_CODING_AGENT_DIR=/pi-agent",
            "-e",
            "PI_OFFLINE=1",
            "-e",
            "HOME=/workspace",
            "-w",
            "/workspace",
        ]
        for key, value in request.env.items():
            command.extend(["-e", f"{key}={value}"])
        command.append(image_reference(request.image, request.digest))
        command.extend(request.pi_args)

        started_at = utc_now()
        started_perf = time.perf_counter()
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            subprocess.run(
                [runtime_binary(), "rm", "-f", name], capture_output=True, text=True, check=False
            )
            stdout = _coerce_text(exc.stdout)
            stderr = _coerce_text(exc.stderr)
            exit_code = None
        wall_clock = round(time.perf_counter() - started_perf, 3)

        (artifact_dir / "pi-output.jsonl").write_text(stdout, encoding="utf-8")
        (artifact_dir / "pi-stderr.txt").write_text(stderr, encoding="utf-8")
        (artifact_dir / "request.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "timeout_seconds": request.timeout_seconds,
                    "timed_out": timed_out,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        parsed = parse_pi_output(stdout)
        if timed_out:
            agent_status = "timeout"
        elif exit_code == 0 and parsed["chat_history"] and parsed["stop_reason"] != "error":
            agent_status = "success"
        else:
            agent_status = "error"
        return {
            "agent_status": agent_status,
            "exit_code": exit_code,
            "wall_clock_seconds": wall_clock,
            "network": self.network,
            **parsed,
        }

    def pi_version(self, image: str, digest: str | None) -> str | None:
        completed = subprocess.run(
            [
                runtime_binary(),
                "run",
                "--rm",
                "--network",
                "none",
                image_reference(image, digest),
                "pi",
                "--version",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None


class StubAgentRunner:
    """Deterministic no-model runner for check.sh and tests."""

    network = "stub"

    def run(self, request: AgentRunRequest, artifact_dir: Path) -> JsonDict:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        marker = request.workspace / "STUB.md"
        signature = " ".join(request.pi_args[-1:])[:64]
        variant = "-".join(request.pi_args[:8])
        marker.write_text(f"stub edit for {variant}\n", encoding="utf-8")
        text = f"Stub response ({variant}) for prompt: {signature}"
        (artifact_dir / "pi-output.jsonl").write_text("", encoding="utf-8")
        (artifact_dir / "pi-stderr.txt").write_text("", encoding="utf-8")
        (artifact_dir / "request.json").write_text(
            json.dumps({"command": ["stub", *request.pi_args]}, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "agent_status": "success",
            "exit_code": 0,
            "wall_clock_seconds": 0.0,
            "network": self.network,
            "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total_tokens": 0},
            "chat_history": [
                {"role": "user", "text": signature},
                {"role": "assistant", "text": text},
            ],
            "response_models": ["stub"],
            "stop_reason": "end_turn",
            "parse_errors": [],
        }

    def pi_version(self, image: str, digest: str | None) -> str | None:
        return "stub"


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
