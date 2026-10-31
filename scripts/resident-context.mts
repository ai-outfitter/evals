/** Formats an authenticated, already selected graph using the shared SDK. No retrieval. */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
let stdin = '';
for await (const chunk of process.stdin) stdin += chunk;
const input = JSON.parse(stdin);
if (!process.env.EVAL_WEBAPP_CHECKOUT) throw new Error('EVAL_WEBAPP_CHECKOUT required');
const { renderGraphTemplates } = await import(pathToFileURL(join(process.env.EVAL_WEBAPP_CHECKOUT, 'packages/app-sdk/src/graph-templates.ts')).href);
const template = JSON.parse(readFileSync(new URL('../experiments/resident-executive-documents/context-template.json', import.meta.url), 'utf8'));
const graph = input.prepared.graph;
const actor = input.prepared.actors[input.actor];
const schema = input.document.schema;
const charterSchema = input.prepared.charterType.schema;
const record = { ref: { installationId: 'evals-local', type: 'context-template', id: 'executive-documents-v1' },
  revision: 1, title: 'Local experiment context template', attributes: { body: JSON.stringify(template) }, references: [] };
const parameters = { agentId: actor.agentId, organizationId: input.prepared.organizationNodeId,
  assignmentId: actor.assignmentId,
  documentSchema: input.document.typeId === input.prepared.charterType.typeId ? null : schema, charterSchema,
  controls: [...(input.prepared.controls ?? [])].sort((a,b) => a.ref.id.localeCompare(b.ref.id)),
  charter: input.charter ? { id: input.charter.id, revision: input.charter.revision, document: input.charter.document } : null, documentKind: input.document.kind ?? null,
  relationships: [...graph.edges].sort((a,b) => a.ref.id.localeCompare(b.ref.id)).map(edge => ({ id: edge.ref.id, revision: edge.revision,
    type: graph.types.find(type => type.ref.id === edge.attributes.typeId)?.attributes.name ?? edge.attributes.typeId, fromId: edge.attributes.fromId, toId: edge.attributes.toId })),
  relationshipTypes: graph.types.filter(type => type.attributes.category === 'edge').sort((a,b) => a.ref.id.localeCompare(b.ref.id)).map(type => ({ id: type.ref.id, name: type.attributes.name })) };
let result;
try {
  result = renderGraphTemplates({ installationId: input.prepared.wikiInstallationId,
    ...graph, templates: [{ record, definition: template }], parameters });
} catch (error) {
  throw new Error(error instanceof Response ? await error.text() : String(error));
}
const sources = [...new Map([...result.sources, ...[...graph.types,...graph.edges,...(input.prepared.controls??[])].map(t => ({ ref: t.ref, revision: t.revision }))]
  .map(source => [JSON.stringify(source.ref), source])).values()];
process.stdout.write(JSON.stringify({ ...result, sources, agentId: actor.agentId,
  organizationId: parameters.organizationId, includesCharterSchema: true, includesDocumentSchema: true,
  complete: result.complete && !graph.incomplete,
  template, parameters, graph }));
