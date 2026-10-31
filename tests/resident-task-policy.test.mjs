import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { residentTaskInstructions, residentTaskResult, selectResidentTaskTools } from '../scripts/resident-task-policy.mjs';

test('task routing scope distinguishes platform and graph organizations without repeating graph context', () => {
  const input = { organizationId: 'platform-org', documentOrganizationId: 'wiki-org-node',
    wikiInstallationId: 'wiki-installation', identity: { assignmentId: 'author-assignment' },
    instructions: 'Draft the plan.', systemContext: 'private factual graph', mcp: true };
  const prompt = residentTaskInstructions(input);
  assert.ok(prompt.includes('"mcpOrganizationId":"platform-org"'));
  assert.ok(prompt.includes('"documentOrganizationId":"wiki-org-node"'));
  assert.ok(prompt.includes('"wikiInstallationId":"wiki-installation"'));
  assert.ok(prompt.includes('"authorAssignmentId":"author-assignment"'));
  assert.ok(prompt.includes('whoami'));
  assert.ok(!prompt.includes(input.systemContext));
  assert.ok(!residentTaskInstructions({ ...input, mcp: false }).includes('whoami'));
});

test('ordinary document tasks expose completion without workflow writes or other task access', async () => {
  const names = ['a2a_read_task', 'a2a_complete_task', 'a2a_record_output', 'a2a_list_tasks', 'a2a_cancel_task', 'future_tool'];
  let transitions = 0;
  const tools = names.map(name => ({ name, execute() { transitions++; }, parameters: { properties: {
    outputs: { type: 'array', items: { type: 'object', properties: { output: { type: 'string' },
      value: { type: 'object', additionalProperties: true } } } },
  } } }));
  const selected = selectResidentTaskTools(tools, 'review');
  assert.deepEqual(selected.map(tool => tool.name), ['a2a_read_task', 'a2a_complete_task']);
  assert.equal(selected[0], tools[0]);
  assert.deepEqual(selected[1].parameters.properties.outputs.items.properties.value.required, ['input', 'documentId', 'revision']);
  assert.equal(selected[1].parameters.properties.outputs.items.properties.output.const, 'evaluation');
  assert.equal(tools[1].parameters.properties.outputs.items.properties.value.required, undefined);
  assert.equal(tools.length, names.length);
  await assert.rejects(selected[1].execute('call', { outcome: 'completed' }), /requires the structured/);
  assert.equal(transitions, 0);
  await selected[1].execute('call', { outcome: 'rejected' });
  await selected[1].execute('call', { outcome: 'completed', outputs: [{ output: 'evaluation', value: {} }] });
  assert.equal(transitions, 2);
  const request = selectResidentTaskTools(tools, 'request')[1];
  assert.deepEqual(request.parameters.properties.outputs.items.properties.value.properties.request.required, ['documentId', 'revision']);
  const schema = { type: 'object', required: ['cadence'], additionalProperties: false, properties: {
    organization_id: { type: 'string' }, status: { type: 'string' },
    owner_assignment_id: { type: 'string' }, cadence: { enum: ['annual'] },
    approval: { type: ['object', 'null'] }, kind: { enum: ['roadmap', 'engineering'] },
  } };
  const scope = { documentSchema: schema, documentKind: 'roadmap', documentOrganizationId: 'wiki-org', identity: { assignmentId: 'role-assignment' } };
  const revision = selectResidentTaskTools(tools, 'revision', scope)[1];
  assert.deepEqual(revision.parameters.properties.outputs.items.properties.value.required, ['document', 'feedbackDisposition']);
  const body = revision.parameters.properties.outputs.items.properties.value.properties.document;
  assert.deepEqual(body.required, ['cadence', 'organization_id', 'status', 'owner_assignment_id', 'kind']);
  assert.equal(body.properties.organization_id.const, scope.documentOrganizationId);
  assert.equal(body.properties.owner_assignment_id.const, scope.identity.assignmentId);
  assert.equal(body.properties.status.const, 'draft');
  assert.equal(body.properties.approval.const, null);
  assert.equal(body.properties.kind.const, 'roadmap');
  assert.deepEqual(body.properties.cadence, schema.properties.cadence);
  assert.equal(body.additionalProperties, false);
  assert.deepEqual(schema.required, ['cadence']);
  assert.equal(schema.properties.status.const, undefined);
});

test('only a single named structured artifact supplies the result; response text is never parsed', () => {
  const value = { input: ['actual peer feedback'], documentId: 'draft', revision: 1 };
  const artifact = { name: 'evaluation', parts: [{ data: value }] };
  assert.equal(residentTaskResult({ artifacts: [{ name: 'response', parts: [{ text: 'Human completion note' }] }, artifact] }), value);
  for (const artifacts of [[], [{ name: 'response', parts: [{ text: JSON.stringify(value) }] }],
    [artifact, artifact], [{ name: 'evaluation', parts: [{ data: JSON.stringify(value) }] }]]) {
    assert.throws(() => residentTaskResult({ artifacts }), /exactly one structured/);
  }
});

test('all task driver local module imports are staged by the remote adapter', () => {
  const driver = readFileSync(new URL('../scripts/resident-task.mjs', import.meta.url), 'utf8');
  const adapter = readFileSync(new URL('../scripts/resident-adapter.py', import.meta.url), 'utf8');
  for (const [, name] of driver.matchAll(/from '\.\/([^']+)'/g)) {
    assert.ok(adapter.includes(JSON.stringify(name)), `Missing remote dependency: ${name}`);
  }
});
