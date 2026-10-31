import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { readOnlyMcpRelay } from '../scripts/resident-mcp-relay.mjs';
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
    assert.equal((await send('call_app_action',{organizationId:'org',installationId:'wiki',action:'knowledge-graph.select'})).status,200);
    for(const args of [
      {organizationId:'org',installationId:'wiki',action:'graph.apply'},
      {organizationId:'org',installationId:'wiki',action:'governance.decide'},
      {organizationId:'other',installationId:'wiki',action:'knowledge-graph.select'},
      {organizationId:'org',installationId:'other',action:'knowledge-graph.select'},
    ]) assert.equal((await send('call_app_action',args)).status,403);
    assert.equal((await send('create_entity',{organizationId:'org',installationId:'wiki'})).status,403);
    assert.equal(calls,1);
    assert.ok(!JSON.stringify(audit).includes('private-token'));
  } finally { await relay.close(); await new Promise(resolve => upstream.close(resolve)); }
});
