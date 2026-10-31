# Real-graph executive document evaluations

This experiment reuses native A2A, Channels' queue, Pi native MCP, and the shared
Wiki graph selector and SDK template renderer. The model does not select or
rewrite factual system context. Start with Outfitter collaboration, then Artera.

```sh
nix develop --command python3 -m evals.cli resident validate \
  experiments/resident-executive-documents/resident.yaml
nix develop --command python3 -m evals.cli resident run \
  experiments/resident-executive-documents/resident.yaml \
  --config /private/config.json --output /private/runs \
  --run-id outfitter-collaboration-v1 --variant collaboration
```

The private CLI config contains `company` (authenticated organization ID) and
`adapter` (argv array, never a shell command), for example
`["python3", "/checkout/scripts/resident-adapter.py", "/private/adapter-config.json"]`.

## Real inputs

Capture an authenticated `knowledge-graph.select` result with explicit
organization, resident, board, plan and relevant project roots. Retain the graph,
selection definition, source revisions and omissions. Fetch the actual
`BoardCharter` and `Plan` graph-type records through `get_entity`, including JSON
schemas. Do not substitute local seeds for current production records.

Resolve CPO/CTO assignments through exact `assigned_to`, `assigns_role` and
`scoped_to` relationships. Require one active matching assignment. Verify each
runtime's MCP `whoami` against the existing private canonical Wiki identity link,
using its identity service or a read-only operator audit of those fields.
Display names and public properties do not prove identity. Captured evidence
does not grant authority or replace server authorization.

The prepared snapshot contains:

- `organizationId`, `organizationNodeId`, `organizationSlug`, `wikiInstallationId`.
- `graph`: EntityRecord `types`, `nodes`, `edges`, `incomplete`; `controls`: stored
  control EntityRecords.
- `actors.luce` and `actors.vega`: canonical `agentId`, `assignmentId`,
  `assignmentRevision`, `assignmentEdges` (IDs/revisions), and
  `canonicalIdentityProof`: `source: wiki-canonical-identity-read`, `readOnly: true`,
  `principalId`, `holderId`, authoritative source and capture time.
- `charter`: null or exact current charter/revision/body;
  `charterType`: `typeId` and actual `schema`.
- `documentTypes`: `product_roadmap_lifecycle` and
  `engineering_tech_stack_strategy`, each with `typeId` and actual `schema`.

This adapter consumes a **frozen authenticated snapshot**. Refresh before a new
dataset revision. Live MCP searches may see later revisions and remain visible
in transcripts. Bootstrap a missing charter once, then pin the same revision for
controlled variant comparisons. Initial bootstrap is functional evidence, not a
matched performance comparison.

## Isolated candidate runtime

Use Channels 1.13.1 or later for trusted system context and ordinary structured
result artifacts. For a candidate, pack its pinned source with `npm pack
--ignore-scripts`. Stage it in a dedicated temporary pod directory and link its
`node_modules` to existing runtime dependencies. Record pod UID, image digest,
package/source hashes and Pi version. Preserve the registered resident process,
queue, authored prompt and configuration; no rollout or release is required.

Private adapter configuration provides `preparedSnapshot`, `evidenceDirectory`,
`connectorCommand`, `webappCheckout`, and `actors` entries with Kubernetes
`context`, `namespace`, explicit `pod`, optional `container`, candidate `package`
and dedicated remote `directory`. The existing DGX provider/model are retained. Explicit versioned eval overrides
currently disable thinking through qwen-chat-template compatibility flags and cap
output at 8192 tokens; this does not select a different model or provider. GitHub write tokens are removed from the subprocess environment;
no repository tools are provided.

The included task adapter creates a separate native listener, store and
TaskSessionHost, submits `/message:send` and polls `/tasks/{id}`. It preserves a
stable message ID and verifies duplicate acceptance. Pi uses the resident's
existing native MCP credentials. This is distinct from webapp scheduler or public
`tasks.submit` acceptance.

Each completion supplies one `evaluation` data artifact containing the requested
JSON object. Response text is a completion note and is never parsed as data.
The eval-local completion tool checks stage fields and the stored document schema
before settling a task, with the verified organization/owner and draft state fixed;
schema, exact revision and current assignment checks still run in the coordinator
and persistence adapter. Task scope distinguishes MCP platform organization IDs
from Wiki organization-node IDs. Neither metadata nor context grants authority.

## Adapter and connected persistence

One JSON request/response per process implements `prepare`, `context`, `task`,
and `save`. Requests have stable `requestKey`s. Completed steps are reused;
unknown outcomes retry the original key/store. Changed accepted requests require
a new run. A lock permits one coordinator per run. Authors request input and
peers answer in separate queued tasks; neither waits synchronously on the other.

The trusted `connectorCommand` is an argv array for a host-owned authenticated MCP
transport. It accepts one JSON `{name, arguments}` request on stdin and returns
the decoded action/entity response on stdout. For the existing managed MCP
transport, use `["node", "/checkout/scripts/resident-mcp.mjs"]` in a trusted
operator environment with its existing MCP URL/token. Its principal must have
current organization access and draft-write permission. Missing authentication
fails visibly; do not grant an eval resident admin rights to bypass it.
Credentials remain in that process and are never sent to model sessions.
There is no manually serviced mailbox. The adapter restricts calls to its exact
organization/installation and generates single-draft `graph.apply` operations.

**The included persistence adapter saves as the authenticated operator.** The
resident authors the document; Wiki records the actual writer, with run/author/task provenance in the supported summary field. Each save rechecks assignment/relationship revisions, validates
the schema and re-reads the saved body. Receipts record
`authorityMode: authenticated-operator-save` and `formalResidentRoleWrite: false`.
This does not demonstrate independent resident-role Governance writes. A backend
using authorized resident draft actions can replace persistence without changing
orchestration. Never grant agents editor/admin rights to make an eval pass.

Drafts are unique per run, idempotent on retry and updated only within that run.
They do not enter unrelated automatic controls, adopt a charter, record agreement
or approve a roadmap. Preserve their evidence during cleanup.

## Evidence and grading

`report.md` and `artifact-manifest.json` link/hash coordinator evidence. The
adapter exports sanitized accepted envelopes, provider-bound system prompts,
transcripts, tool activity, task outputs and runtime provenance before cleanup.
Private company data stays outside Git. Never archive auth/config credentials;
preserve failed attempts and capture gaps.

Deterministic checks cover JSON Schema, organization/owner, exact peer revision,
feedback disposition, retry behavior and draft-only state. Human rubric review
covers grounding, usefulness, feasibility and cross-document consistency; schema
validity does not establish quality or truth. The report leaves quality review
pending.

`baseline` disables MCP/peer tasks; `retrieval` enables MCP; `collaboration` adds
one reciprocal input round. Keep graph/charter, model and scorer fixed when
comparing a single variable. Report fixture tests, real inference, native MCP
use, operator saves, resident-authorized writes and formal control execution
separately. One shared-DGX run is not a performance benchmark.

## Runtime stability and inspection

`scripts/resident-eval-pod.py` creates an isolated temporary Job from the resident's
pinned image. It preserves the managed workload and copies only required provider
configuration into private storage. Missing provider environment credentials fail
before inference. Capture evidence before deleting only the eval-owned Jobs.

Run `evals resident inspect --evidence /private/evidence --port 4397` for the
loopback evidence browser. Status distinguishes timeouts, interruptions,
authentication/configuration failures and waiting for persistence; expired step
deadlines appear stalled. Old immutable attempts retain their original failure
records. Raw transcripts and composed system prompts remain private.

A connected host can retain its existing OAuth connection using
`scripts/resident-connected-host.py serve /private/connector.sock`. Configure
`connectorCommand` as `["python3", "/checkout/scripts/resident-connected-host.py",
"call", "/private/connector.sock"]`. The host automatically dispatches `next`
requests through its connected Outfitter tools and supplies each decoded result
with `respond`. The Unix socket is owner-only; no OAuth token is exported. If the
host stops servicing it, calls time out visibly and resume uses the original keys.
This bridge requires an active connected host; it is not an unattended OAuth daemon.
