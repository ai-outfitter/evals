/** Run inside the resident: credentials remain in its existing environment. */
let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const url = process.env.OUTFITTER_MANAGED_MCP_URL;
const token = process.env.OUTFITTER_MANAGED_MCP_TOKEN;
if (!url || !token) throw new Error('Resident managed MCP is not configured');
const response = await fetch(url, {
  method: 'POST', signal: AbortSignal.timeout(180000),
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: request }),
});
if (!response.ok) throw new Error(`MCP HTTP ${response.status}`);
const raw = await response.text();
const messages = response.headers.get('content-type')?.includes('text/event-stream')
  ? raw.split('\n').filter(line => line.startsWith('data:')).map(line => JSON.parse(line.slice(5)))
  : [JSON.parse(raw)];
const message = messages.find(item => item.id === 1);
if (!message || message.error || message.result?.isError) throw new Error(JSON.stringify(message ?? { error: 'Missing MCP response' }));
const texts = (message.result?.content ?? []).filter(item => item.type === 'text').map(item => item.text);
const result = texts.length === 1 ? JSON.parse(texts[0]) : message.result;
process.stdout.write(JSON.stringify(result));
