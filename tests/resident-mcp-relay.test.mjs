import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { nativeMcpVerified, readOnlyMcpRelay, restoreMcpAudit } from '../scripts/resident-mcp-relay.mjs';
test('same-task restart retains successful native identity evidence without a new call', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'resident-audit-'));
  const path = join(directory, 'mcp-audit.json');
  try {
    assert.deepEqual(await restoreMcpAudit(path), []);
    await writeFile(path, JSON.stringify([{ tool: 'outfitter_identity_get', verifiedPrincipal: 'resident:original' }]));
    const restored = await restoreMcpAudit(path);
    assert.equal(nativeMcpVerified(restored, 'resident:original'), true);
    assert.equal(nativeMcpVerified(restored, 'resident:other'), false);
    assert.equal(nativeMcpVerified([{tool: 'outfitter_identity_get', httpStatus: 200}], 'resident:original'), false);
    await writeFile(path, '{}');
    await assert.rejects(restoreMcpAudit(path), /Invalid persisted/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
test('native MCP relay permits scoped reads and prevents writes or cross-organization access', async () => {
  let calls = 0;
  const upstream = createServer(async (request, response) => {
    calls++; assert.equal(request.headers.authorization, 'Bearer private-token');
    response.writeHead(200, {'content-type':'application/json'}).end(JSON.stringify({jsonrpc:'2.0', id:1, result:{content:[]}}));
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const audit = [], relay = await readOnlyMcpRelay({url:`http://127.0.0.1:${upstream.address().port}`,token:'private-token',organizationId:'org',installationIds:['wiki'],audit});
  try {
    assert.equal((await fetch(relay.url,{method:'POST',body:'{}'})).status,401);
    const send = (name, args) => fetch(relay.url, {method:'POST',headers:{Authorization:`Bearer ${relay.transportToken}`},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})});
    assert.equal((await send('outfitter_app_action_call',{organizationId:'org',installationId:'wiki',action:'knowledge-graph.select'})).status,200);
    for(const args of [
      {organizationId:'org',installationId:'wiki',action:'graph.apply'},
      {organizationId:'org',installationId:'wiki',action:'governance.decide'},
      {organizationId:'other',installationId:'wiki',action:'knowledge-graph.select'},
      {organizationId:'org',installationId:'other',action:'knowledge-graph.select'},
    ]) assert.equal((await send('outfitter_app_action_call',args)).status,403);
    assert.equal((await send('graph_action',{organizationId:'org',installationId:'wiki'})).status,403);
    assert.equal(calls,1);
    assert.ok(!JSON.stringify(audit).includes('private-token'));
  } finally { await relay.close(); await new Promise(resolve => upstream.close(resolve)); }
});

test('identity verification requires a successful native outfitter_identity_get result, including SSE responses', async () => {
  let mode = 'json';
  const upstream = createServer((request, response) => {
    const rpc = { jsonrpc: '2.0', id: 1, result: {
      ...(mode === 'error' ? { isError: true } : {}),
      content: [{ type: 'text', text: JSON.stringify({ id: 'resident:verified' }) }],
    } };
    response.writeHead(mode === 'http-error' ? 401 : 200, {
      'content-type': mode === 'sse' ? 'text/event-stream' : 'application/json',
    }).end(mode === 'sse' ? `event: message\ndata: ${JSON.stringify(rpc)}\n\n` : JSON.stringify(rpc));
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const audit = [], relay = await readOnlyMcpRelay({
    url: `http://127.0.0.1:${upstream.address().port}`, token: 'private-token',
    organizationId: 'org', installationIds: ['wiki'], audit,
  });
  try {
    for (const variant of ['json', 'sse', 'error', 'http-error']) {
      mode = variant;
      await fetch(relay.url, { method: 'POST', headers: { Authorization: `Bearer ${relay.transportToken}` },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'outfitter_identity_get', arguments: {} } }),
      });
      assert.equal(audit.at(-1).verifiedPrincipal, ['json', 'sse'].includes(mode) ? 'resident:verified' : undefined);
    }
    assert.ok(!JSON.stringify(audit).includes('private-token'));
  } finally { await relay.close(); await new Promise(resolve => upstream.close(resolve)); }
});

test('advertised JSON and SSE tool catalogs contain only callable read-policy tools', async () => {
  let mode = 'json';
  const upstream = createServer((request, response) => {
    const rpc = { jsonrpc: '2.0', id: 1, result: { tools: [
      { name: 'outfitter_identity_get' }, { name: 'outfitter_entity_get' },
      { name: 'outfitter_app_action_call' }, { name: 'outfitter_entity_create' },
      { name: 'planning_product_search' }, { name: 'governance_action' },
    ] } };
    response.writeHead(200, { 'content-type': mode === 'sse' ? 'text/event-stream' : 'application/json' })
      .end(mode === 'sse' ? `event: message\ndata: ${JSON.stringify(rpc)}\n\n` : JSON.stringify(rpc));
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const relay = await readOnlyMcpRelay({ url: `http://127.0.0.1:${upstream.address().port}`,
    token: 'private-token', organizationId: 'org', installationIds: ['wiki'] });
  try {
    for (mode of ['json', 'sse']) {
      const response = await fetch(relay.url, { method: 'POST',
        headers: { Authorization: `Bearer ${relay.transportToken}` },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) });
      const raw = await response.text();
      const rpc = JSON.parse(mode === 'sse' ? raw.split('\n').find(line => line.startsWith('data:')).slice(5) : raw);
      assert.deepEqual(rpc.result.tools.map(tool => tool.name),
        ['outfitter_identity_get', 'outfitter_entity_get', 'outfitter_app_action_call']);
    }
  } finally { await relay.close(); await new Promise(resolve => upstream.close(resolve)); }
});
