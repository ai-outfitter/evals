/** Native A2A canary adapter. Run in a resident pod, with its existing credentials.
 * Input: {requestKey, systemContext, instructions, evidence, mcp, timeoutSeconds}.
 * Paths below must be private. Keep the directory until evidence is exported.
 */
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile, mkdir, copyFile, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readOnlyMcpRelay } from './resident-mcp-relay.mjs';
let stdin = '';
for await (const chunk of process.stdin) stdin += chunk;
const input = JSON.parse(stdin);
assert.match(input.requestKey, /^[a-f0-9]{64}$/);
const root = process.env.EVAL_CHANNELS_PACKAGE;
const base = process.env.EVAL_RESIDENT_DIRECTORY;
assert.ok(root && base, 'EVAL_CHANNELS_PACKAGE and EVAL_RESIDENT_DIRECTORY required');
const directory = join(base, input.requestKey);
await mkdir(directory, { recursive: true, mode: 0o700 });
const original = process.env.PI_CODING_AGENT_DIR;
assert.ok(original);
try {
  const providerEnv = JSON.parse(await readFile(join(original,'provider-env.json'),'utf8'));
  for(const [name,value] of Object.entries(providerEnv)) {
    assert.ok(!/GITHUB|GH_TOKEN/.test(name), 'Repository credentials cannot enter an eval');
    process.env[name] = value;
  }
} catch(error) { if(error.code !== 'ENOENT') throw error; }
const secrets = Object.entries(process.env).filter(([key]) => /TOKEN|SECRET|PASSWORD|API_KEY|AUTHORIZATION|CREDENTIAL/.test(key)).map(([,v]) => v).filter(v => v.length > 8);
const sanitize = value => {
  let text = JSON.stringify(value);
  for (const secret of secrets) text = text.replaceAll(secret, '[REDACTED]');
  return JSON.parse(text);
};
const save = async (file, value) => writeFile(join(directory, file), JSON.stringify(sanitize(value), null, 2), { mode: 0o600 });
const from = path => pathToFileURL(join(root, path)).href;
const { ModelRuntime } = await import(from('node_modules/@earendil-works/pi-coding-agent/dist/index.js'));
const { TaskSessionHost } = await import(from('extensions/task-plane/task-sessions.ts'));
const { startChannelsRuntime } = await import(from('extensions/task-plane/runtime.ts'));
const { createA2aRuntimeListener, registerA2aTools } = await import(from('extensions/a2a-extension.ts'));
const agentDir = join(directory, 'agent');
await mkdir(agentDir, { recursive: true, mode: 0o700 });
for (const file of ['models.json', 'auth.json', 'settings.json']) await copyFile(join(original, file), join(agentDir, file));
const mcpAudit = [];
const mcpRelay = input.mcp ? await readOnlyMcpRelay({ url: process.env.OUTFITTER_MANAGED_MCP_URL,
  token: process.env.OUTFITTER_MANAGED_MCP_TOKEN, organizationId: input.organizationId,
  installationIds: [input.wikiInstallationId], audit: mcpAudit }) : null;
if (mcpRelay) {
  secrets.push(mcpRelay.transportToken);
  await writeFile(join(agentDir,'mcp.json'), JSON.stringify({mcpServers:{outfitter:{url:mcpRelay.url,
    headers:{Authorization:`Bearer ${mcpRelay.transportToken}`},enabled:true,exposure:'direct'}}}), {mode:0o600});
}
const settings = JSON.parse(await readFile(join(agentDir, 'settings.json'), 'utf8'));
// Do not start the registered resident's channel extensions in this isolated process.
settings.packages = [];
settings.defaultTools = ['a2a_read_task', 'a2a_complete_task', ...(input.mcp ? ['mcp__outfitter__whoami', 'mcp__outfitter__list_app_actions', 'mcp__outfitter__call_app_action', 'mcp__outfitter__list_organizations', 'mcp', 'tool_search'] : [])];
await writeFile(join(agentDir, 'settings.json'), JSON.stringify(settings), { mode: 0o600 });
const authored = await readFile(join(original, 'SYSTEM.md'), 'utf8');
const models = await ModelRuntime.create({ modelsPath: join(agentDir, 'models.json'), authPath: join(agentDir, 'auth.json') });
assert.ok(models.hasConfiguredAuth(settings.defaultProvider), 'Existing resident provider credential is unavailable');
let model = models.getModel(settings.defaultProvider, settings.defaultModel);
assert.ok(model, 'Configured resident model not available');
const overrides = input.modelSettings ?? {};
model = {...model, ...(overrides.max_tokens ? {maxTokens:overrides.max_tokens}:{}),
  ...(overrides.thinking_format ? {compat:{...model.compat,thinkingFormat:overrides.thinking_format}}:{})};
const thinking = overrides.thinking ?? settings.defaultThinkingLevel;
// Probe the same workload credential before inference. Native session tool presence
// is observed separately below: transport readiness alone is not native MCP evidence.
let mcpProbe = null;
if (input.mcp) {
  const r = await fetch(process.env.OUTFITTER_MANAGED_MCP_URL, {
    method: 'POST', signal: AbortSignal.timeout(30000),
    headers: { Authorization: `Bearer ${process.env.OUTFITTER_MANAGED_MCP_TOKEN}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'whoami', arguments: {} } }),
  });
  const raw = await r.text();
  const message = raw.split('\n').find(line => line.startsWith('data:'));
  mcpProbe = message ? JSON.parse(message.slice(5)) : JSON.parse(raw);
  assert.ok(r.ok && !mcpProbe.error && !mcpProbe.result?.isError, 'Resident MCP identity probe failed');
  const identity = JSON.parse(mcpProbe.result.content.find(item => item.type === 'text').text);
  assert.equal(identity.id, input.identity.canonicalIdentityProof.principalId, 'Task runtime principal changed');
  await save('mcp-identity.json', mcpProbe);
}
await save('accepted-input.json', input);
const probePath = join(directory, 'probe.mjs');
await writeFile(probePath, `import {appendFileSync,readFileSync} from 'node:fs'; export default function(api){const graph=JSON.parse(readFileSync(${JSON.stringify(join(directory,'accepted-input.json'))},'utf8')).systemContext;api.on('before_provider_request',event=>{const p=event.payload; const system=Array.isArray(p.messages)?p.messages.filter(x=>x.role==='system'):p.system;const tools=p.tools??[];appendFileSync(${JSON.stringify(join(directory,'provider-observations.jsonl'))},JSON.stringify({at:Date.now(),system,nonSystemContainsGraph:JSON.stringify((p.messages??[]).filter(x=>x.role!=='system')).includes(JSON.stringify(graph).slice(1,-1)),tools:tools.map(t=>t.function?.name??t.name),parameters:{model:p.model,max_tokens:p.max_tokens,max_completion_tokens:p.max_completion_tokens,reasoning_effort:p.reasoning_effort,chat_template_kwargs:p.chat_template_kwargs}})+'\\n',{mode:0o600});});}`);
let server, queue;
const tools = [], token = randomUUID(), events = [];
registerA2aTools({ registerTool: tool => tools.push(tool) }, () => server,
  async id => await queue?.hasAuthority(id) ?? false, () => true);
const turns = new TaskSessionHost({ cwd: directory, agentDir, sessionDir: join(directory, 'sessions'),
  customTools: tools, additionalExtensionPaths: [probePath], excludedExtensionRoot: root,
  model: () => model, thinkingLevel: () => thinking,
  promptSources: { systemPrompt: authored }, log: event => events.push(event) });
const runtime = await startChannelsRuntime({ sendUserMessage: () => { throw new Error('Unexpected parent wake'); } }, {
  storePath: join(directory, 'task-plane'), agentInterface: 'http://127.0.0.1', sources: [], taskTurnRunner: turns,
  taskPlaneReady: (_plane, q) => { queue = q; },
  listener: createA2aRuntimeListener({ enabled: () => true, loadConfig: async () => ({ host: '127.0.0.1', port: 0,
    storePath: join(directory, 'a2a-store.json'), credentials: [{ token, principal: 'executive-eval' }],
    agentName: 'executive-eval', agentDescription: 'Isolated resident evaluation', agentVersion: 'candidate', publicUrl: 'http://127.0.0.1' }) }, s => { server = s; }),
});
const headers = { Authorization: `Bearer ${token}`, 'content-type': 'application/a2a+json', 'a2a-version': '1.0' };
const instructions = `${input.instructions}\n\n${input.mcp ? 'Call the native Outfitter MCP whoami tool once to verify your current acting principal. ' : ''}Complete this A2A task with a2a_complete_task. Put the requested JSON object in its response. Do not approve/adopt documents.\n\nTask evidence:\n${JSON.stringify(input.evidence ?? {})}`;
// Stable message ID is retained across process restarts and unknown-outcome retries.
const body = { message: { messageId: input.requestKey, role: 'ROLE_USER', parts: [{ text: JSON.stringify({ kind: 'outfitter.resident-task/v1', instructions, systemContext: input.systemContext }) }] }, configuration: { returnImmediately: true } };
await save('accepted-envelope.json', body);
const started = Date.now();
let capturedAt = 0;
async function checkpoint() {
  const files = {};
  for (const name of ['accepted-input.json','accepted-envelope.json','accepted-task.json','task.json']) {
    try { files[name] = JSON.parse(await readFile(join(directory,name),'utf8')); } catch {}
  }
  try { files['provider-observations.json'] = (await readFile(join(directory,'provider-observations.jsonl'),'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse); } catch {}
  const transcripts = [];
  try { for (const name of await readdir(join(directory,'sessions'),{recursive:true})) {
    if (name.endsWith('.jsonl')) transcripts.push({path:name,content:await readFile(join(directory,'sessions',name),'utf8')});
  } } catch {}
  files['transcripts.json'] = transcripts;
  files['mcp-audit.json'] = mcpAudit;
  process.stdout.write(JSON.stringify({event:'evidence',files:sanitize(files)})+'\n');
  capturedAt = Date.now();
}
let current;
try {
  const sent = await fetch(new URL('/message:send', server.url), { method: 'POST', headers, body: JSON.stringify(body) });
  assert.equal(sent.status, 200, 'Native A2A submission failed');
  const { task } = await sent.json(); assert.ok(task?.id);
  await save('accepted-task.json', task);
  const repeated = await fetch(new URL('/message:send', server.url), { method: 'POST', headers, body: JSON.stringify(body) });
  assert.equal((await repeated.json()).task.id, task.id);
  while (Date.now() - started < input.timeoutSeconds * 1000) {
    const response = await fetch(new URL(`/tasks/${task.id}`, server.url), { headers });
    assert.ok(response.ok); current = await response.json();
    await save('task.json', current);
    if (Date.now() - capturedAt > 10000) await checkpoint();
    if (['TASK_STATE_COMPLETED', 'TASK_STATE_FAILED', 'TASK_STATE_REJECTED', 'TASK_STATE_CANCELED'].includes(current.status?.state)) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  await turns.close();
  const observations = (await readFile(join(directory, 'provider-observations.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  // Rewrite observations through redaction before the bundle is exported.
  await save('provider-observations.json', observations);
  await rm(join(directory, 'provider-observations.jsonl'), { force: true });
  const transcripts = [];
  for (const file of await readdir(join(directory, 'sessions'), { recursive: true })) {
    if (file.endsWith('.jsonl')) transcripts.push({ path: file, content: await readFile(join(directory, 'sessions', file), 'utf8') });
  }
  await save('transcripts.json', transcripts);
  const messages = transcripts.flatMap(file => file.content.trim().split('\n').map(line => JSON.parse(line)).map(entry => entry.message).filter(Boolean));
  const usage = messages.filter(message => message.role === 'assistant').reduce((total, message) => ({
    inputTokens: total.inputTokens + (message.usage?.input ?? 0), outputTokens: total.outputTokens + (message.usage?.output ?? 0),
    reasoningTokens: total.reasoningTokens + (message.usage?.reasoning ?? 0),
  }), { inputTokens: 0, outputTokens: 0, reasoningTokens: 0 });
  const text = (current?.artifacts ?? []).flatMap(artifact => artifact.parts ?? []).map(part => part.text ?? '').join('\n');
  let answer;
  try { answer = JSON.parse(text.replace(/^```(?:json)?\s*/, '').replace(/\s*```$/, '')); } catch { answer = { rawResponse: text }; }
  const wire = observations.map(o => JSON.stringify(o.system));
  const toolNames = observations.flatMap(o => o.tools);
  const result = { ...answer, taskId: current.id, status: current.status.state === 'TASK_STATE_COMPLETED' ? 'completed' : 'incomplete',
    elapsedMs: Date.now() - started, systemContextObserved: wire.some(value => value.includes(JSON.stringify(input.systemContext).slice(1,-1))),
    artifactsCaptured: transcripts.length > 0 && observations.length > 0,
    nativeMcpReady: input.mcp ? toolNames.some(name => /mcp|tool_search/.test(name)) : false,
    usage, providerRequests: observations.length, contextBytes: Buffer.byteLength(input.systemContext),
    systemWireBytes: Buffer.byteLength(JSON.stringify(observations[0]?.system)),
    graphDuplicatedOutsideSystem: observations.some(item=>item.nonSystemContainsGraph),
    runtime: { provider: settings.defaultProvider, model: settings.defaultModel, thinking,
      evalOverrides: overrides, maxTokens: model.maxTokens, thinkingFormat: model.compat?.thinkingFormat,
      basePromptDigest: createHash('sha256').update(authored).digest('hex'),
      channelsSourceDigest: createHash('sha256').update(await readFile(join(root,'extensions/a2a-extension.ts'))).digest('hex'),
      node: process.version, channelsPackage: root, piVersion: JSON.parse(await readFile(join(root, 'node_modules/@earendil-works/pi-coding-agent/package.json'))).version },
    duplicateReturnsSameTask: true, artifactDirectory: directory };
  await save('mcp-audit.json', mcpAudit);
  result.mcpCalls = mcpAudit.filter(item => item.method === 'tools/call');
  await save('events.json', events); await save('result.json', result);
  process.stdout.write(JSON.stringify(sanitize(result)));
} finally {
  await runtime.close(); await turns.close();
  await mcpRelay?.close();
  // Raw task stores and sessions may contain tool-returned secrets. Export only sanitized copies.
  for (const file of ['auth.json', 'mcp.json']) await rm(join(agentDir, file), { force: true });
}
