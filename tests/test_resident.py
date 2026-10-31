import copy
import json
from pathlib import Path
import tempfile
import unittest

from evals.resident import ResidentExperiment, ResidentError, check_document, digest, load_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "experiments/resident-executive-documents/resident.yaml"
SCHEMA = {"type": "object", "required": ["organization_id", "status"], "properties": {
    "organization_id": {"type": "string"}, "status": {"enum": ["draft", "adopted"]}}}


class FakeAdapter:
    """Synthetic protocol fixture, never described as resident or Wiki execution."""
    def __init__(self):
        self.calls = []
        self.fail_once = None
        self.saved = {}
        self.stale = False
        self.missing_charter = False

    def call(self, request):
        self.calls.append(copy.deepcopy(request))
        op = request["operation"]
        if self.fail_once == op:
            self.fail_once = None
            raise ResidentError("Simulated unknown outcome")
        if op == "prepare":
            return {"organizationId": "platform-org", "organizationNodeId": "org", "wikiInstallationId": "wiki", "actors": {
                actor: {"verified": True, "agentId": actor, "assignmentId": actor + "-role"}
                for actor in ["luce", "vega"]}, "charterType": {"typeId": "charter", "schema": SCHEMA},
                "charter": None if self.missing_charter else {"id": "existing-charter", "revision": 1, "document": {"status": "draft"}},
                "documents": [{**item, "typeId": "plan", "schema": SCHEMA} for item in request["documents"]]}
        if op == "context":
            return {"agentId": request["actor"], "organizationId": "org", "markdown": "# Stored graph facts\n",
                    "complete": True, "includesCharterSchema": True, "includesDocumentSchema": True,
                    "sources": [{"id": "org", "revision": 1}], "rendererVersion": 1}
        if op == "task":
            evidence = request.get("evidence") or {}
            document = evidence.get("document", {"organization_id":"org", "status":"draft"})
            draft = evidence.get("draft", evidence)
            if "draft" in evidence and "input" in evidence:
                document = evidence["draft"]["document"]
            if "kind" in document:
                document = {"kind": document["kind"], "organization_id": "org", "status": "draft",
                            "owner_assignment_id": request["actor"] + "-role"}
            return {"taskId": request["requestKey"], "status": "completed", "runtime": {"model": "scripted"},
                    "nativeMcpReady": True, "systemContextObserved": True, "artifactsCaptured": True,
                    "document": document, "feedbackDisposition": ["Incorporated fixture input"],
                    "documentId": draft.get("id"), "revision": -1 if self.stale else draft.get("revision"),
                    "request": {"documentId": evidence.get("id"), "revision": evidence.get("revision"),
                                "questions": ["technical dependency?"]}, "input": "Fixture input"}
        if op == "save":
            previous = request.get("previous")
            result = {"id": previous["id"] if previous else request["requestKey"],
                      "revision": previous["revision"] + 1 if previous else 1,
                      "url": "https://example.test/draft", "bodyDigest": digest(request["document"]), "document": request["document"]}
            self.saved[request["requestKey"]] = result
            return result
        raise AssertionError(op)


class ResidentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "run"
        self.spec = load_spec(SPEC)
        self.adapter = FakeAdapter()

    def experiment(self, variant="collaboration"):
        return ResidentExperiment(self.spec, {"company": "synthetic"}, self.path, variant, self.adapter)

    def test_two_documents_reciprocal_input_then_exact_revision_saves(self):
        result = self.experiment().run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([d["revision"] for d in result["documents"]], [2, 2])
        self.assertTrue(result["checks"]["reciprocalInput"])
        tasks = [r for r in self.adapter.calls if r["operation"] == "task"]
        self.assertEqual([r["actor"] for r in tasks], ["luce", "vega", "luce", "vega", "vega", "luce", "luce", "vega"])
        self.assertTrue((self.path / "artifact-manifest.json").exists())
        self.assertFalse(result["formalControlExecution"])

    def test_resume_does_not_redeliver_completed_tasks_or_saves(self):
        self.experiment().run()
        before = len(self.adapter.calls)
        self.assertEqual(self.experiment().run()["status"], "completed")
        self.assertEqual(len(self.adapter.calls), before)

    def test_unknown_outcome_reuses_key_and_preserves_failure(self):
        self.adapter.fail_once = "task"
        self.assertEqual(self.experiment().run()["status"], "configuration_failed")
        first = next(r for r in self.adapter.calls if r["operation"] == "task")
        self.assertEqual(self.experiment().run()["status"], "completed")
        tasks = [r for r in self.adapter.calls if r["requestKey"] == first["requestKey"]]
        self.assertEqual(tasks[0], tasks[1])
        self.assertTrue((self.path / "steps/product-roadmap-initial/failure.json").exists())

    def test_changed_prompt_requires_new_run(self):
        self.experiment().run()
        self.spec["prompts"]["draft"] += " Changed."
        with self.assertRaisesRegex(ResidentError, "changed"):
            self.experiment().run()

    def test_stale_peer_input_blocks_final_revision(self):
        self.adapter.stale = True
        result = self.experiment().run()
        self.assertEqual(result["status"], "configuration_failed")
        self.assertIn("stale", result["error"]["message"])
        self.assertFalse(any(r.get("previous") for r in self.adapter.calls if r["operation"] == "save"))

    def test_cross_organization_and_adoption_rejected(self):
        for body in [{"organization_id": "elsewhere", "status": "draft"},
                     {"organization_id": "org", "status": "adopted"}]:
            with self.assertRaises(ResidentError):
                check_document(body, SCHEMA, "org")

    def test_baseline_has_no_mcp_or_peer_tasks(self):
        result = self.experiment("baseline").run()
        self.assertEqual(result["status"], "completed")
        tasks = [r for r in self.adapter.calls if r["operation"] == "task"]
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(not r["mcp"] for r in tasks))
        self.assertEqual([d["revision"] for d in result["documents"]], [1, 1])

    def test_missing_charter_is_reviewed_and_saved_without_adoption(self):
        self.adapter.missing_charter = True
        result = self.experiment().run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["charter"]["revision"], 2)
        self.assertEqual(result["charter"]["document"]["status"], "draft")

    def test_stale_charter_feedback_prevents_revision(self):
        self.adapter.missing_charter = True
        self.adapter.stale = True
        result = self.experiment().run()
        self.assertEqual(result["status"], "configuration_failed")
        self.assertIn("Charter peer input", result["error"]["message"])

    def test_run_provenance_is_not_the_variant(self):
        self.path = self.path / "actual-run-id" / "collaboration"
        self.experiment().run()
        saves = [x for x in self.adapter.calls if x["operation"] == "save"]
        self.assertEqual(saves[0]["provenance"]["run"], "actual-run-id")

    def test_failed_task_includes_checkpoint_artifacts(self):
        runtime = Path(self.tmp.name) / "adapter/checkpoint/runtime"
        runtime.mkdir(parents=True)
        (runtime / "transcripts.json").write_text("[]")
        original = self.adapter.call
        def interrupted(request):
            if request["operation"] == "task":
                raise ResidentError("Runtime interrupted", runtime)
            return original(request)
        self.adapter.call = interrupted
        self.assertEqual(self.experiment().run()["status"], "runtime_interrupted")
        manifest = json.loads((self.path / "artifact-manifest.json").read_text())
        self.assertTrue(any(row["path"].endswith("runtime/transcripts.json") for row in manifest["artifacts"]))

    def test_manifest_hashes_actual_evidence(self):
        self.experiment().run()
        manifest = json.loads((self.path / "artifact-manifest.json").read_text())
        import hashlib
        for artifact in manifest["artifacts"]:
            self.assertEqual(artifact["sha256"], hashlib.sha256((self.path / artifact["path"]).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
