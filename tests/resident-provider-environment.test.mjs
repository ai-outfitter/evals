import assert from 'node:assert/strict';
import test from 'node:test';
import { providerEnvironment } from '../scripts/resident-provider-environment.mjs';

test('selected provider references support bare and braced variables without copying unrelated credentials', () => {
  const provider = { apiKey: '$SPARK_AUTHORIZATION', headers: { actor: '${AGENT_NAME}', repeated: '$AGENT_NAME' },
    unused: '${MISSING}' };
  assert.deepEqual(providerEnvironment(provider, { SPARK_AUTHORIZATION: 'synthetic-provider-key',
    AGENT_NAME: 'synthetic-agent', GITHUB_TOKEN: 'synthetic-repository-key', OTHER_SECRET: 'other' }),
  { SPARK_AUTHORIZATION: 'synthetic-provider-key', AGENT_NAME: 'synthetic-agent' });
});

test('repository credential references are rejected in either variable format', () => {
  for (const apiKey of ['$GITHUB_TOKEN', '${GH_TOKEN}'])
    assert.throws(() => providerEnvironment({ apiKey }, {}), /Repository credentials/);
});
