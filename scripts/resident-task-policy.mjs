// These evaluations submit ordinary A2A tasks, without a workflow manifest.
// Workflow output tools cannot operate on them; persistence is handled outside
// the model session by the authenticated adapter.
export function selectResidentTaskTools(tools, resultKind, scope) {
  const allowed = new Set(['a2a_read_task', 'a2a_complete_task']);
  const required = { document: ['document'], revision: ['document', 'feedbackDisposition'],
    review: ['input', 'documentId', 'revision'], request: ['request'] }[resultKind];
  if (!required) throw new Error('Unknown resident evaluation result kind');
  let document = { type: 'object' };
  if (resultKind === 'document' || resultKind === 'revision') {
    if (!scope?.documentSchema || !scope.documentOrganizationId || !scope.identity?.assignmentId)
      throw new Error('Document completion requires its stored schema and verified scope');
    document = { ...scope.documentSchema, required: [...new Set([...(scope.documentSchema.required ?? []), 'organization_id', 'status'])],
      properties: { ...scope.documentSchema.properties,
        organization_id: { ...scope.documentSchema.properties.organization_id, const: scope.documentOrganizationId },
        status: { ...scope.documentSchema.properties.status, const: 'draft' },
      } };
    if (document.properties.owner_assignment_id) {
      if (!document.required.includes('owner_assignment_id')) document.required.push('owner_assignment_id');
      document.properties.owner_assignment_id = { ...document.properties.owner_assignment_id, const: scope.identity.assignmentId };
    }
    if (document.properties.approval) document.properties.approval = { ...document.properties.approval, const: null };
    if (document.properties.kind) {
      if (!scope.documentKind) throw new Error('Plan completion requires its selected document kind');
      document.properties.kind = { ...document.properties.kind, const: scope.documentKind };
      if (!document.required.includes('kind')) document.required.push('kind');
    }
  }
  return tools.filter(tool => allowed.has(tool.name)).map(tool => {
    if (tool.name !== 'a2a_complete_task') return tool;
    const outputs = tool.parameters.properties.outputs;
    return { ...tool, async execute(...args) {
      if (args[1].outcome === 'completed' && !args[1].outputs?.length)
        throw new Error('Completion requires the structured evaluation output; response is only a completion note');
      return tool.execute(...args);
    }, parameters: { ...tool.parameters, properties: { ...tool.parameters.properties,
      outputs: { ...outputs, minItems: 1, maxItems: 1, items: { ...outputs.items, properties: {
        ...outputs.items.properties, output: { type: 'string', const: 'evaluation' },
        value: { ...outputs.items.properties.value, required, properties: {
          document, request: { type: 'object', required: ['documentId', 'revision'],
            properties: { documentId: { type: 'string' }, revision: { type: 'integer' } } },
          documentId: { type: 'string' }, revision: { type: 'integer' }, input: {},
          feedbackDisposition: { anyOf: [{ type: 'string', minLength: 1 }, { type: 'array', minItems: 1 }, { type: 'object', minProperties: 1 }] },
        } },
      } } },
    } } };
  });
}

export const completionInstructions = 'Complete this ordinary A2A task with a2a_complete_task: set outcome to "completed", response to a short completion note, and outputs to [{"output":"evaluation","value": the requested JSON object}]. value must be a JSON object, not a serialized string. This is an ordinary structured artifact, not a workflow output. Do not call workflow output tools. Do not approve/adopt documents.';

export function residentTaskResult(task) {
  const values = (task.artifacts ?? []).filter(artifact => artifact.name === 'evaluation')
    .flatMap(artifact => artifact.parts ?? []).filter(part => part.data !== undefined).map(part => part.data);
  if (values.length !== 1 || !values[0] || typeof values[0] !== 'object' || Array.isArray(values[0]))
    throw new Error('Completed task must have exactly one structured evaluation artifact');
  return values[0];
}

export function residentTaskInstructions(input) {
  const scope = {
    mcpOrganizationId: input.organizationId,
    wikiInstallationId: input.wikiInstallationId,
    documentOrganizationId: input.documentOrganizationId,
    authorAssignmentId: input.identity.assignmentId,
  };
  return `${input.instructions}\n\nTask scope:\n${JSON.stringify(scope)}\n\nUse mcpOrganizationId for MCP organizationId arguments and wikiInstallationId for Wiki installationId arguments. For generated documents include organization_id from documentOrganizationId; for plans include owner_assignment_id from authorAssignmentId. These references grant no write or approval authority.\n\nThe factual system context already contains the selected organization graph, role assignments and document schemas. Task evidence below contains the exact saved draft and actual peer feedback when applicable. Use these supplied records directly; app discovery or reloading the graph is unnecessary. Retrieve only specific missing facts if needed; record unresolved facts as open questions. A failed optional lookup does not prevent drafting or reviewing the supplied evidence.\n\n${input.mcp ? 'Call the native Outfitter MCP outfitter_identity_get tool once to verify your current acting principal. ' : ''}${completionInstructions}\n\nTask evidence:\n${JSON.stringify(input.evidence ?? {})}`;
}
