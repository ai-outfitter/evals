"""Resumable executive-document experiments over a credential-separated resident adapter.

The adapter performs authorized graph selection, rendering, native A2A delivery
and Wiki persistence. This coordinator never receives runtime credentials.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import re
import subprocess
import shutil
import time
from pathlib import Path

import jsonschema
import yaml


class ResidentError(Exception):
    def __init__(self, message, artifact_directory=None):
        super().__init__(message)
        self.artifact_directory = artifact_directory


def failure_state(message: str) -> str:
    message = message.lower()
    if "timed out" in message or "timeout" in message or "task not completed" in message:
        return "timed_out"
    if "interrupted" in message or "transport failed" in message:
        return "runtime_interrupted"
    if any(word in message for word in ("authentication", "credential", "principal", "unauthorized", "forbidden")):
        return "authentication_failed"
    if any(phrase in message for phrase in ("schema validation", "result validation", "document organization", "document kind", "draft owner", "structured document")):
        return "validation_failed"
    return "configuration_failed"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_spec(path: Path) -> dict:
    value = yaml.safe_load(path.read_text())
    schema = Path(__file__).resolve().parents[2] / "schemas/resident-experiment.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema.read_text())).validate(value)
    expected = {("luce", "cpo", "product_roadmap_lifecycle"),
                ("vega", "cto", "engineering_tech_stack_strategy")}
    if {(d["actor"], d["role"], d["kind"]) for d in value["documents"]} != expected:
        raise ResidentError("Experiment requires Luce/CPO roadmap and Vega/CTO engineering strategy")
    return value


class CommandAdapter:
    """One JSON request/response per process; no shell evaluation of configuration."""

    def __init__(self, command: list[str], timeout: int):
        if not command or not all(isinstance(item, str) for item in command):
            raise ResidentError("adapter must be a nonempty argv array")
        self.command, self.timeout = command, timeout

    def call(self, request: dict) -> dict:
        try:
            result = subprocess.run(self.command, input=json.dumps(request), text=True,
                                    capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise ResidentError("Adapter timed out; delivery outcome unknown. Resume the same run/key.") from error
        # stderr may contain credentials from a failing transport: never publish it.
        if result.returncode:
            raise ResidentError(f"Adapter exited {result.returncode}; inspect its private transport log")
        try:
            response = json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise ResidentError("Adapter did not return one JSON response") from error
        if not isinstance(response, dict):
            raise ResidentError("Adapter response must be an object")
        if response.get("error"):
            raise ResidentError(str(response["error"]), response.get("artifactDirectory"))
        return response


def check_document(document: dict, schema: dict, organization_id: str,
                   kind: str | None = None) -> None:
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: str(list(error.path)))
    if errors:
        # Error paths, not possibly private rejected values, are sufficient here.
        raise ResidentError("Schema validation failed at " + ", ".join(
            "/".join(map(str, error.path)) or "/" for error in errors))
    if document.get("organization_id") != organization_id:
        raise ResidentError("Document organization does not match selected graph")
    if document.get("status") != "draft":
        raise ResidentError("Evaluation documents must remain drafts")
    if document.get("approval") is not None:
        raise ResidentError("Evaluation drafts cannot record board approval")
    if kind and document.get("kind") != kind:
        raise ResidentError("Document kind does not match responsible role")


def verify_context(context: dict, prepared: dict, actor: str) -> None:
    if context.get("agentId") != prepared["actors"][actor]["agentId"]:
        raise ResidentError("Context root is not the verified resident")
    if context.get("organizationId") != prepared["organizationNodeId"]:
        raise ResidentError("Context crosses organization boundary")
    markdown = context.get("markdown", "")
    if not isinstance(markdown, str) or not markdown or len(markdown.encode()) > 48 * 1024:
        raise ResidentError("Context missing or exceeds 48 KiB")
    if not context.get("complete"):
        raise ResidentError("Incomplete context: inspect omitted/unresolved references")
    if not context.get("includesCharterSchema") or not context.get("includesDocumentSchema"):
        raise ResidentError("Context must include charter and document JSON schemas")
    if not context.get("sources") or not context.get("rendererVersion"):
        raise ResidentError("Context requires source revisions and renderer provenance")


class ResidentExperiment:
    def __init__(self, spec: dict, configuration: dict, directory: Path,
                 variant: str, adapter=None):
        self.spec, self.configuration, self.directory = spec, configuration, directory
        self.variant = variant
        self.adapter = adapter or CommandAdapter(configuration["adapter"], spec["timeout_seconds"] + 60)
        if variant not in spec["variants"]:
            raise ResidentError("Unknown experiment variant")
        self.state: dict = {}

    def capture_runtime(self, path: Path, source_directory) -> None:
        if not source_directory:
            return
        source = Path(source_directory)
        for filename in ("accepted-input.json", "accepted-envelope.json", "accepted-task.json",
                         "provider-observations.json", "transcripts.json", "task.json", "events.json",
                         "result.json", "mcp-identity.json", "mcp-audit.json", "capture-gap.json"):
            artifact = source / filename
            if artifact.is_file() and not artifact.is_symlink():
                destination = path / "runtime" / filename
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(artifact, destination)
                destination.chmod(0o600)

    def step(self, name: str, operation: str, payload: dict) -> dict:
        path = self.directory / "steps" / name
        request = {"operation": operation, "requestKey": digest({"run": str(self.directory.resolve()),
                   "step": name}), "variant": self.variant, **payload}
        request_path, result_path = path / "request.json", path / "result.json"
        if request_path.exists() and json.loads(request_path.read_text()) != request:
            raise ResidentError(f"Immutable accepted step changed: {name}; start a new run")
        if result_path.exists():
            return json.loads(result_path.read_text())
        # Persist before delivery so interruption/retry uses the identical key and envelope.
        write(request_path, request)
        write(self.directory / "progress.json", {"step": name, "operation": operation, "startedAt": time.time(),
              "deadline": time.time() + self.spec["timeout_seconds"] + 60,
              "state": "waiting_for_persistence" if operation == "save" else "running"})
        started = time.monotonic()
        try:
            result = self.adapter.call(request)
            if operation == "task":
                self.capture_runtime(path, result.get("artifactDirectory"))
            if operation == "task" and result.get("status") != "completed":
                write(path / "last-observation.json", result)
                if result.get("status") == "invalid_response":
                    raise ResidentError("Structured result validation failed; inspect the captured task artifacts")
                raise ResidentError("Task not completed; resume with the same accepted key")
            write(result_path, result)
            write(self.directory / "progress.json", {"step": name, "state": "step_completed", "updatedAt": time.time()})
            write(path / "timing.json", {"elapsedMs": round((time.monotonic() - started) * 1000)})
            return result
        except BaseException as error:
            source = getattr(error, "artifact_directory", None)
            if not source and self.configuration.get("runtimeArtifactRoot"):
                source = Path(self.configuration["runtimeArtifactRoot"]) / request["requestKey"] / "runtime"
            self.capture_runtime(path, source)
            write(path / "failure.json", {"type": type(error).__name__, "message": str(error),
                                          "retry": "Resume same run; preserve unknown delivery outcome"})
            raise

    def context(self, prepared: dict, actor: str, document: dict, charter: dict | None) -> dict:
        name = document["key"]
        result = self.step(f"context-{actor}-{name}", "context", {
            "actor": actor, "prepared": prepared, "document": document, "charter": charter})
        verify_context(result, prepared, actor)
        return {**result, "documentSchema": document["schema"], "documentKind": document.get("kind")}

    def task(self, name: str, prepared: dict, actor: str, context: dict,
             instruction: str, evidence: dict | None = None) -> dict:
        result = self.step(name, "task", {"actor": actor, "identity": prepared["actors"][actor],
            "organizationId": prepared["organizationId"], "wikiInstallationId": prepared["wikiInstallationId"],
            "documentOrganizationId": prepared["organizationNodeId"],
            "resultKind": "request" if name.endswith("-request-input") else "review" if name.endswith("-peer-input") else "revision" if name.endswith("-revise") else "document",
            "documentSchema": context["documentSchema"],
            "documentKind": context["documentKind"],
            "modelSettings": self.spec.get("model_settings", {}),
            "systemContext": context["markdown"], "contextDigest": digest(context),
            "instructions": instruction, "evidence": evidence,
            "mcp": self.variant != "baseline", "timeoutSeconds": self.spec["timeout_seconds"]})
        if result.get("status") != "completed":
            raise ResidentError(f"Task {name} did not complete")
        if not result.get("systemContextObserved") or not result.get("artifactsCaptured"):
            raise ResidentError("Runtime must capture composed system prompt and transcript before cleanup")
        if not result.get("taskId") or not result.get("runtime"):
            raise ResidentError("Missing task/runtime provenance")
        if self.variant != "baseline" and not result.get("nativeMcpReady"):
            raise ResidentError("Native MCP was not verified for task session")
        return result

    def save(self, name: str, prepared: dict, actor: str, document: dict,
             response: dict, previous: dict | None = None) -> dict:
        body = response.get("document")
        if not isinstance(body, dict):
            raise ResidentError("Task must return a structured document")
        check_document(body, document["schema"], prepared["organizationNodeId"], document.get("kind"))
        if document.get("kind") and body.get("owner_assignment_id") != prepared["actors"][actor]["assignmentId"]:
            raise ResidentError("Draft owner must be the verified current role assignment")
        result = self.step(name, "save", {"actor": actor, "organizationId": prepared["organizationId"],
            "documentTypeId": document["typeId"], "document": body, "previous": previous,
            "assignmentId": prepared["actors"][actor]["assignmentId"],
            "provenance": {"experiment": self.spec["id"], "variant": self.variant,
                           "run": self.directory.parent.name if self.directory.name == self.variant else self.directory.name, "taskId": response["taskId"]}})
        if not result.get("id") or not isinstance(result.get("revision"), int) or not result.get("url"):
            raise ResidentError("Save requires canonical Wiki ID, revision and URL")
        if result.get("bodyDigest") != digest(body):
            raise ResidentError("Saved Wiki revision differs from validated generated document")
        return {**result, "document": body}

    def run(self) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / "run.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ResidentError("This run already has an active coordinator") from error
            return self._run()

    def _run(self) -> dict:
        manifest = {"experiment": self.spec, "variant": self.variant,
                    "configurationDigest": digest(self.configuration),
                    "inputs": [{"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                               for path in self.configuration.get("inputs", [])]}
        manifest_path = self.directory / "manifest.json"
        if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
            raise ResidentError("Run specification/configuration changed; choose a new run directory")
        write(manifest_path, manifest)
        outcome = {"status": "running", "variant": self.variant, "documents": [], "checks": {},
                   "formalControlExecution": False, "approval": "pending"}
        write(self.directory / "outcome.json", outcome)
        try:
            prepared = self.step("prepare", "prepare", {"company": self.configuration["company"],
                                 "documents": self.spec["documents"]})
            for actor in ("luce", "vega"):
                identity = prepared["actors"][actor]
                if not identity.get("verified") or not identity.get("assignmentId"):
                    raise ResidentError(f"{actor}: missing verified identity/current role")
            charter = prepared.get("charter")
            charter_type = prepared["charterType"]
            if not charter:
                charter_document = {"key": "charter", **charter_type}
                context = self.context(prepared, "luce", charter_document, None)
                draft = self.task("charter-initial", prepared, "luce", context,
                    self.spec["prompts"]["charter"])
                receipt = self.save("charter-save-initial", prepared, "luce", charter_document, draft)
                if self.variant == "collaboration":
                    peer_context = self.context(prepared, "vega", charter_document, None)
                    feedback = self.task("charter-peer-input", prepared, "vega", peer_context,
                        self.spec["prompts"]["review"], receipt)
                    if (feedback.get("documentId"), feedback.get("revision")) != (receipt["id"], receipt["revision"]):
                        raise ResidentError("Charter peer input refers to a stale or different document")
                    revised = self.task("charter-revise", prepared, "luce", context,
                        self.spec["prompts"]["revise"], {"draft": receipt, "input": feedback})
                    if not revised.get("feedbackDisposition"):
                        raise ResidentError("Charter revision must explain peer feedback disposition")
                    receipt = self.save("charter-save-final", prepared, "luce", charter_document, revised, receipt)
                charter = receipt
            drafts, contexts = {}, {}
            for document in prepared["documents"]:
                key, actor = document["key"], document["actor"]
                contexts[key] = self.context(prepared, actor, document, charter)
                response = self.task(f"{key}-initial", prepared, actor, contexts[key],
                    self.spec["prompts"]["draft"], {"document": document, "charter": charter})
                drafts[key] = self.save(f"{key}-save-initial", prepared, actor, document, response)
            feedbacks = {}
            if self.variant == "collaboration":
                # Persist both requests before dispatch; peers never synchronously wait.
                for document in prepared["documents"]:
                    key, actor = document["key"], document["actor"]
                    peer = "vega" if actor == "luce" else "luce"
                    peer_document = next(item for item in prepared["documents"] if item["actor"] == peer)
                    request = self.task(f"{key}-request-input", prepared, actor, contexts[key],
                        self.spec["prompts"]["request_input"], drafts[key])
                    if not request.get("request"):
                        raise ResidentError("Author did not produce an explicit peer input request")
                    if (request["request"].get("documentId"), request["request"].get("revision")) != (drafts[key]["id"], drafts[key]["revision"]):
                        raise ResidentError("Peer request does not reference the exact saved draft revision")
                    feedbacks[key] = self.task(f"{key}-peer-input", prepared, peer,
                        contexts[peer_document["key"]], self.spec["prompts"]["review"],
                        {"request": request["request"], "draft": drafts[key]})
                    if (feedbacks[key].get("documentId"), feedbacks[key].get("revision")) != (drafts[key]["id"], drafts[key]["revision"]):
                        raise ResidentError("Peer input refers to a stale or different document")
                for document in prepared["documents"]:
                    key, actor = document["key"], document["actor"]
                    revised = self.task(f"{key}-revise", prepared, actor, contexts[key],
                        self.spec["prompts"]["revise"], {"draft": drafts[key], "input": feedbacks[key]})
                    if not revised.get("feedbackDisposition"):
                        raise ResidentError("Revision must explain how actual peer input was handled")
                    drafts[key] = self.save(f"{key}-save-final", prepared, actor, document, revised, drafts[key])
            outcome.update(status="completed", documents=list(drafts.values()), charter=charter,
                checks={"schemaValid": True, "savedLiveDrafts": True,
                        "reciprocalInput": len(feedbacks) == 2,
                        "humanQualityReview": "pending", "groundingReview": "pending"})
        except BaseException as error:
            outcome.update(status=failure_state(str(error)) if isinstance(error, ResidentError) else "failed",
                           error={"type": type(error).__name__, "message": str(error)})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            write(self.directory / "outcome.json", outcome)
            render_report(self.directory)
        return outcome


def render_report(directory: Path) -> None:
    outcome = json.loads((directory / "outcome.json").read_text())
    lines = ["# Resident executive document evaluation", "", f"Status: **{outcome['status']}**", "",
             "Human approval/adoption: pending. Formal control execution is separately verified.", ""]
    for record in outcome.get("documents", []):
        lines.append(f"- [Wiki draft revision {record['revision']}]({record['url']})")
    if outcome.get("error"):
        lines.extend(["", "```json", json.dumps(outcome["error"], indent=2), "```"])
    lines.extend(["", "## Artifacts", ""])
    artifacts = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in {"report.md", "artifact-manifest.json"}:
            continue
        relative = path.relative_to(directory).as_posix()
        artifacts.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "bytes": path.stat().st_size})
        lines.append(f"- [{relative}]({relative})")
    write(directory / "artifact-manifest.json", {"artifacts": artifacts, "attested": False})
    (directory / "report.md").write_text("\n".join(lines) + "\n")


def main(args) -> int:
    try:
        spec = load_spec(args.experiment)
        if args.resident_command == "validate":
            print(f"ok: {spec['id']}")
            return 0
        config = json.loads(args.config.read_text())
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", args.run_id):
            raise ResidentError("Invalid run ID")
        directory = args.output.resolve() / spec["id"] / args.run_id / args.variant
        outcome = ResidentExperiment(spec, config, directory, args.variant).run()
        print(f"{outcome['status']}: {directory / 'report.md'}")
        return 0 if outcome["status"] == "completed" else 1
    except (ResidentError, jsonschema.ValidationError, ValueError, KeyError) as error:
        print(f"resident eval: {error}")
        return 1
