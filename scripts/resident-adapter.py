"""Private-config adapter for frozen authenticated graph snapshots and resident canaries.

The trusted connector command uses existing authenticated MCP access outside
the model process. No manual mailbox or additional approval registry is used. Persistence records that
actor explicitly: operator saves must not be described as resident-role writes.
"""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import threading
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from evals.resident import ResidentError, digest, write

ROOT = Path(__file__).resolve().parents[1]
config = json.loads(Path(sys.argv[1]).read_text())
request = json.load(sys.stdin)
directory = Path(config["evidenceDirectory"]) / "adapter" / request["requestKey"]
directory.mkdir(parents=True, exist_ok=True, mode=0o700)


def command(argv, payload, timeout=660):
    result = subprocess.run(argv, input=json.dumps(payload), text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Keep transport diagnostics private; credentials are never copied to stdout.
        write(directory / "transport-failure.json", {"exitCode": result.returncode,
            "diagnosticCodes": re.findall(r"\b(?:E[A-Z]{3,20}|SyntaxError|TypeError|ReferenceError|AssertionError|TimeoutError)\b", result.stderr),
            "diagnosticDigest": digest(result.stderr), "captureGap": "Raw transport stderr excluded to avoid credential disclosure"})
        raise ResidentError("Resident transport failed; inspect private transport-failure.json")
    return json.loads(result.stdout)


def kube(actor):
    runtime = config["actors"][actor]
    return ["kubectl", "--context", runtime["context"], "exec", "-i", "-n", runtime["namespace"],
            runtime["pod"], "-c", runtime.get("container", "agent"), "--"]


def mcp(actor, name, arguments):
    return command(kube(actor) + ["node", "--input-type=module", "-e", (ROOT / "scripts/resident-mcp.mjs").read_text()],
                   {"name": name, "arguments": arguments}, timeout=200)


def task_command(argv, payload):
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(payload["timeoutSeconds"] + 45, process.kill)
    timer.start()
    result = None
    try:
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "evidence":
                for name, value in event["files"].items():
                    if name in {"accepted-input.json", "accepted-envelope.json", "accepted-task.json", "task.json",
                                "transcripts.json", "provider-observations.json", "mcp-audit.json"}:
                        write(directory / "runtime" / name, value)
            elif "artifactDirectory" in event:
                result = event
        code = process.wait()
        if code or result is None:
            write(directory / "runtime" / "capture-gap.json", {"exitCode": code,
                "message": "Runtime interrupted; last checkpoint retained; final response may be unavailable"})
            raise ResidentError("Runtime interrupted; inspect captured partial transcript")
        return result
    finally:
        timer.cancel()


def connector(name, arguments, suffix):
    """Run trusted authenticated MCP transport outside the model process."""
    argv = config.get("connectorCommand")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ResidentError("Persistence authentication configuration missing: connectorCommand required")
    prepared = json.loads(Path(config["preparedSnapshot"]).read_text())
    if name not in {"whoami", "get_entity", "call_app_action"}:
        raise ResidentError("Unsupported persistence operation")
    if name != "whoami" and (arguments.get("organizationId"), arguments.get("installationId")) != (
            prepared["organizationId"], prepared["wikiInstallationId"]):
        raise ResidentError("Persistence crosses organization boundary")
    response = command(argv, {"name": name, "arguments": arguments}, timeout=180)
    if response.get("error"):
        raise ResidentError("Authenticated persistence operation failed")
    write(directory / (suffix + "-response.json"), response)
    return response


def execute():
    operation = request["operation"]
    if operation == "prepare":
        prepared = json.loads(Path(config["preparedSnapshot"]).read_text())
        if request["company"] != prepared["organizationId"]:
            raise ResidentError("Snapshot does not belong to requested company")
        for actor, identity in prepared["actors"].items():
            observed = mcp(actor, "whoami", {})
            proof = identity.get("canonicalIdentityProof", {})
            if observed.get("id") != proof.get("principalId") or proof.get("holderId") != identity["agentId"]:
                raise ResidentError("Runtime principal does not match canonical Wiki identity evidence")
            if proof.get("source") != "wiki-canonical-identity-read" or not proof.get("readOnly"):
                raise ResidentError("Missing canonical identity evidence; titles/configuration are insufficient")
            identity["verified"] = True
        prepared["snapshotMode"] = "frozen-authenticated-source"
        prepared["documents"] = [{**item, **prepared["documentTypes"][item["kind"]]} for item in request["documents"]]
        return prepared
    if operation == "context":
        env = dict(os.environ, EVAL_WEBAPP_CHECKOUT=config["webappCheckout"])
        node = config.get("node", "node")
        result = subprocess.run([node, "--import", str(Path(config["webappCheckout"]) / "node_modules/tsx/dist/loader.mjs"),
                                 str(ROOT / "scripts/resident-context.mts")], input=json.dumps(request),
                                capture_output=True, text=True, env=env, timeout=30)
        if result.returncode:
            write(directory / "render-failure.json", {"stderr": result.stderr})
            raise ResidentError("Shared SDK renderer failed; inspect render-failure.json")
        return json.loads(result.stdout)
    if operation == "task":
        actor = request["actor"]
        runtime = config["actors"][actor]
        driver = runtime["directory"] + "/drivers/" + request["requestKey"]
        command(kube(actor) + ["node", "--input-type=module", "-e", """
import{mkdirSync,writeFileSync}from'node:fs';import{join}from'node:path';
let input='';for await(const c of process.stdin)input+=c;
const x=JSON.parse(input);mkdirSync(x.directory,{recursive:true,mode:448});
for(const [name,text] of Object.entries(x.files))writeFileSync(join(x.directory,name),text,{mode:384});
process.stdout.write('{}');
"""], {"directory": driver, "files": {name:(ROOT/"scripts"/name).read_text() for name in ["resident-task.mjs", "resident-mcp-relay.mjs"]}}, timeout=45)
        argv = kube(actor) + ["env", "-u", "AI_OUTFITTER_GITHUB_TOKEN", "-u", "GITHUB_TOKEN", "-u", "GH_TOKEN", "EVAL_CHANNELS_PACKAGE=" + runtime["package"],
                "EVAL_RESIDENT_DIRECTORY=" + runtime["directory"], "node", "--experimental-strip-types",
                driver + "/resident-task.mjs"]
        result = task_command(argv, request)
        # Export only sanitized artifacts, before any remote cleanup. Raw runtime
        # stores and auth/config files never leave the resident.
        remote = result["artifactDirectory"]
        exported = command(kube(actor) + ["node", "--input-type=module", "-e", """
import {readFileSync} from 'node:fs';import {join} from 'node:path';
let input='';for await(const c of process.stdin)input+=c;
const {directory}=JSON.parse(input);const result={};
for(const name of ['accepted-input.json','accepted-envelope.json','accepted-task.json','provider-observations.json','transcripts.json','task.json','events.json','result.json','mcp-identity.json','mcp-audit.json']){
 try{result[name]=JSON.parse(readFileSync(join(directory,name),'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}}
process.stdout.write(JSON.stringify(result));
"""], {"directory": remote}, timeout=45)
        for name, value in exported.items():
            write(directory / "runtime" / name, value)
        result["artifactDirectory"] = str(directory / "runtime")
        result["artifactManifest"] = [{"path": name, "digest": digest(value)} for name, value in exported.items()]
        return result
    if operation == "save":
        prepared = json.loads(Path(config["preparedSnapshot"]).read_text())
        actor = prepared["actors"][request["actor"]]
        # Explicitly re-read current assignment before each save, including retries.
        args = {"organizationId": prepared["organizationId"], "installationId": prepared["wikiInstallationId"],
                "type": "graph-node", "id": actor["assignmentId"]}
        current = connector("get_entity", args, "assignment")
        if current["data"]["revision"] != actor["assignmentRevision"] or current["data"]["attributes"].get("archived"):
            raise ResidentError("Role assignment changed; refresh sources and start a new experiment")
        for edge in actor["assignmentEdges"]:
            link = connector("get_entity", {**args, "type": "graph-edge", "id": edge["id"]}, "edge-" + edge["id"])
            if link["data"]["revision"] != edge["revision"] or link["data"]["attributes"].get("archived"):
                raise ResidentError("Role assignment relationship changed; start a fresh experiment")
        # The authenticated operator owns this save; the resident authors it.
        writer = connector("whoami", {}, "writer")
        previous = request.get("previous")
        node_id = previous["id"] if previous else str(uuid.uuid5(uuid.NAMESPACE_URL, request["requestKey"]))
        title = f"Eval {request['provenance']['run']} — {request['actor']} — {request['document'].get('kind', 'board charter')}"
        attributes = {"typeId": request["documentTypeId"], "title": title[:160],
          "body": json.dumps(request["document"], ensure_ascii=False),
          "summary": f"Unapproved eval draft. Run {request['provenance']['run']}; author {actor['agentId']}; task {request['provenance']['taskId']}; variant {request['provenance']['variant']}"[:280]}
        op = {"ref": "$draft", "type": "graph-node", "attributes": attributes,
              "operation": "update" if previous else "create"}
        if previous:
            op.update(id=node_id, revision=previous["revision"])
        else:
            attributes["id"] = node_id
        receipt = connector("call_app_action", {"organizationId": prepared["organizationId"],
          "installationId": prepared["wikiInstallationId"], "action": "graph.apply",
          "input": {"requestKey": "eval-" + request["requestKey"], "operations": [op]}}, "save")
        write(directory / "save-receipt.json", receipt)
        saved = connector("get_entity", {**args, "id": node_id}, "saved")
        record = saved["data"]
        return {"id": node_id, "revision": record["revision"],
          "url": f"https://ai-outfitter.com/o/{prepared['organizationSlug']}/wiki-map/entities/{node_id}",
          "document": json.loads(record["attributes"]["body"]),
          "bodyDigest": digest(json.loads(record["attributes"]["body"])),
          "savedBy": writer["user"]["id"], "authorship": actor["agentId"],
          "authorityMode": "authenticated-operator-save", "formalResidentRoleWrite": False}
    raise ResidentError("Unknown adapter operation")


try:
    print(json.dumps(execute()))
except Exception as error:
    print(json.dumps({"error": str(error), "type": type(error).__name__}))
