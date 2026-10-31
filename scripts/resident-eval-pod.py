"""Create a temporary eval Job from an existing resident's pinned image/credentials.

No Agent resource, identity link, service or resident registration is created.
The source workload is read-only; profile credentials stream pod-to-pod.
"""
import argparse
import json
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--context", required=True)
p.add_argument("--namespace", required=True)
p.add_argument("--source-pod", required=True)
p.add_argument("--job", required=True)
args = p.parse_args()
base = ["kubectl", "--context", args.context, "-n", args.namespace]
source = json.loads(subprocess.check_output(base + ["get", "pod", args.source_pod, "-o", "json"]))
agent = next(c for c in source["spec"]["containers"] if c["name"] == "agent")
if "@sha256:" not in agent["image"]:
    raise SystemExit("Source runtime must use an immutable image digest")
allowed_env = {"AGENT_NAME", "AGENT_SLUG", "AGENT_ORGANIZATION", "AGENT_ENDPOINT_ID", "AGENT_PRINCIPAL_ID",
               "OUTFITTER_INFERENCE_TOKEN", "OUTFITTER_MANAGED_MCP_TOKEN", "OUTFITTER_MANAGED_MCP_URL"}
env = [e for e in agent.get("env", []) if e["name"] in allowed_env]
env += [{"name": "HOME", "value": "/eval"}, {"name": "PI_CODING_AGENT_DIR", "value": "/eval/original"}]
job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": args.job, "namespace": args.namespace},
       "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 14400, "ttlSecondsAfterFinished": 3600,
        "template": {"metadata": {"labels": {"app.kubernetes.io/name": "resident-executive-eval"}},
         "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
          "nodeSelector": {"kubernetes.io/hostname": source["spec"]["nodeName"]},
          "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "runAsNonRoot": True},
          "containers": [{"name": "agent", "image": agent["image"], "workingDir": "/eval",
           "command": ["node", "-e", "setInterval(()=>{},60000)"], "env": env,
           "resources": {"requests": {"cpu": "25m", "memory": "128Mi"}, "limits": {"cpu": "150m", "memory": "768Mi"}},
           "volumeMounts": [{"name": "eval", "mountPath": "/eval"}]}],
          "volumes": [{"name": "eval", "emptyDir": {}}]}}}}
subprocess.run(base + ["create", "-f", "-"], input=json.dumps(job), text=True, check=True, stdout=subprocess.DEVNULL)
pods = []
import time
for _ in range(60):
    pods = json.loads(subprocess.check_output(base + ["get", "pods", "-l", "job-name=" + args.job, "-o", "json"]))["items"]
    if pods:
        break
    time.sleep(1)
if len(pods) != 1:
    raise SystemExit("Eval Job must have exactly one pod; inspect its status")
pod = pods[0]["metadata"]["name"]
subprocess.run(base + ["wait", "pod/" + pod, "--for=condition=Ready", "--timeout=120s"], check=True, stdout=subprocess.DEVNULL)
source_exec = base + ["exec", args.source_pod, "-c", "agent", "--"]
destination = base + ["exec", "-i", pod, "-c", "agent", "--"]
# Filter the auth store to the configured model provider. Never copy GitHub,
# platform admin, repository credentials, or the source resident's queue/store.
extract = Path(__file__).with_name("resident-provider-environment.mjs").read_text().replace("export function", "function") + r"""
import{readFileSync}from'node:fs';const p=process.env.PI_CODING_AGENT_DIR;
const settings=JSON.parse(readFileSync(p+'/settings.json'));const provider=settings.defaultProvider;
const models=JSON.parse(readFileSync(p+'/models.json'));const auth=JSON.parse(readFileSync(p+'/auth.json'));
const providerEnv=providerEnvironment(models.providers[provider],process.env);
process.stdout.write(JSON.stringify({'SYSTEM.md':readFileSync(p+'/SYSTEM.md','utf8'),
 'provider-env.json':JSON.stringify(providerEnv),
 'settings.json':JSON.stringify({...settings,packages:[]}),
 'models.json':JSON.stringify({...models,providers:{[provider]:models.providers[provider]}}),
 'auth.json':JSON.stringify(auth[provider]?{[provider]:auth[provider]}:{})}));
"""
install = """
import{readFileSync,mkdirSync,writeFileSync}from'node:fs';const p=process.env.PI_CODING_AGENT_DIR;
mkdirSync(p,{recursive:true,mode:448});for(const [name,text]of Object.entries(JSON.parse(readFileSync(0,'utf8'))))
writeFileSync(p+'/'+name,text,{mode:384});
"""
process = subprocess.Popen(source_exec + ["node", "--input-type=module", "-e", extract], stdout=subprocess.PIPE)
try:
    subprocess.run(destination + ["node", "--input-type=module", "-e", install], stdin=process.stdout, check=True)
finally:
    process.stdout.close()
if process.wait():
    raise SystemExit("Source profile copy failed; do not run this candidate")
print(json.dumps({"context": args.context, "namespace": args.namespace, "pod": pod,
                  "sourcePodUid": source["metadata"]["uid"], "image": agent["image"], "job": args.job}))
