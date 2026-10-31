import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { selectResidentTaskTools } from '../scripts/resident-task-policy.mjs';

test('ordinary document tasks expose completion without workflow writes or other task access', () => {
  const names = ['a2a_read_task', 'a2a_complete_task', 'a2a_record_output', 'a2a_list_tasks', 'a2a_cancel_task', 'future_tool'];
  const tools = names.map(name => ({ name, execute() {} }));
  const selected = selectResidentTaskTools(tools);
  assert.deepEqual(selected.map(tool => tool.name), ['a2a_read_task', 'a2a_complete_task']);
  assert.equal(selected[0], tools[0]);
  assert.equal(selected[1], tools[1]);
  assert.equal(tools.length, names.length);
});

test('all task driver local module imports are staged by the remote adapter', () => {
  const driver = readFileSync(new URL('../scripts/resident-task.mjs', import.meta.url), 'utf8');
  const adapter = readFileSync(new URL('../scripts/resident-adapter.py', import.meta.url), 'utf8');
  for (const [, name] of driver.matchAll(/from '\.\/([^']+)'/g)) {
    assert.ok(adapter.includes(JSON.stringify(name)), `Missing remote dependency: ${name}`);
  }
});
