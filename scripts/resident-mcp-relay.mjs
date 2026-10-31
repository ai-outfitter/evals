/** Read-only credential relay; Pi remains the MCP client. No tool implementations. */
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
export async function restoreMcpAudit(path) {
  try {
    const audit = JSON.parse(await readFile(path, 'utf8'));
    if (!Array.isArray(audit)) throw new Error('Invalid persisted MCP audit');
    return audit;
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
}

export const nativeMcpVerified = (audit, principal) => audit.some(item => item.verifiedPrincipal === principal);
const readActions = new Set(['knowledge-graph.select', 'graph.relationships', 'work.context', 'resident.context',
  'projects.list', 'project.explore', 'governance.summary', 'governance.work-policy']);
const readTools = new Set(['whoami', 'list_organizations', 'list_app_actions', 'get_entity', 'list_entities', 'list_entity_types']);
export async function readOnlyMcpRelay({ url, token, organizationId, installationIds, audit = [] }) {
  const transportToken = randomUUID();
  const server = createServer(async (request, response) => {
    try {
      if (request.headers.authorization !== `Bearer ${transportToken}`) {
        response.writeHead(401).end(); return;
      }
      if (request.url !== '/mcp' || request.method !== 'POST') {
        response.writeHead(405).end(); return;
      }
      let bytes = 0; const chunks = [];
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > 128 * 1024) throw new Error('MCP request exceeds eval limit');
        chunks.push(chunk);
      }
      const raw = Buffer.concat(chunks), body = JSON.parse(raw);
      let allowed = ['initialize', 'notifications/initialized', 'ping', 'tools/list'].includes(body.method);
      const tool = body.params?.name, args = body.params?.arguments ?? {};
      if (body.method === 'tools/call') {
        allowed = readTools.has(tool) || tool === 'call_app_action' && readActions.has(args.action);
        if (!['whoami', 'list_organizations'].includes(tool)) {
          allowed &&= args.organizationId === organizationId;
          if (args.installationId) allowed &&= installationIds.includes(args.installationId);
        }
      }
      const observation = { at: Date.now(), method: body.method, tool, action: args.action, allowed };
      audit.push(observation);
      if (!allowed) {
        response.writeHead(403, { 'content-type': 'application/json' }).end(JSON.stringify({ jsonrpc: '2.0',
          id: body.id ?? null, error: { code: -32600, message: 'Evaluation MCP permits scoped reads only' } }));
        return;
      }
      const upstream = await fetch(url, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(180000),
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
          ...(request.headers['mcp-session-id'] ? { 'mcp-session-id': request.headers['mcp-session-id'] } : {}) }, body: raw });
      const result = []; let size = 0;
      if (upstream.body) for await (const chunk of upstream.body) {
        size += chunk.length; if (size > 2 * 1024 * 1024) throw new Error('MCP response exceeds eval limit');
        result.push(chunk);
      }
      const headers = { 'content-type': upstream.headers.get('content-type') ?? 'application/json' };
      observation.httpStatus = upstream.status;
      observation.elapsedMs = Date.now() - observation.at;
      if (upstream.ok && tool === 'whoami') {
        try {
          const text = Buffer.concat(result).toString('utf8');
          const message = text.split('\n').find(line => line.startsWith('data:'));
          const rpc = JSON.parse(message ? message.slice(5) : text);
          if (!rpc.error && !rpc.result?.isError) {
            const identity = JSON.parse(rpc.result.content.find(item => item.type === 'text').text);
            if (typeof identity.id === 'string') observation.verifiedPrincipal = identity.id;
          }
        } catch { /* An undecodable identity response is not verification. */ }
      }
      if (upstream.headers.has('mcp-session-id')) headers['mcp-session-id'] = upstream.headers.get('mcp-session-id');
      response.writeHead(upstream.status, headers).end(Buffer.concat(result));
    } catch {
      response.writeHead(502, { 'content-type': 'application/json' }).end(JSON.stringify({ error: 'Evaluation MCP relay failed; credentials omitted' }));
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { url: `http://127.0.0.1:${server.address().port}/mcp`, transportToken,
    close: () => new Promise(resolve => server.close(resolve)) };
}
