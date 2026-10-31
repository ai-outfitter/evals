// These evaluations submit ordinary A2A tasks, without a workflow manifest.
// Workflow output tools cannot operate on them; persistence is handled outside
// the model session by the authenticated adapter.
export function selectResidentTaskTools(tools) {
  const allowed = new Set(['a2a_read_task', 'a2a_complete_task']);
  return tools.filter(tool => allowed.has(tool.name));
}

export const completionInstructions = 'Complete this ordinary A2A task with a2a_complete_task: set outcome to "completed" and response to a JSON string containing the requested object. Do not supply outputs or call workflow output tools. Do not approve/adopt documents.';

export function residentTaskInstructions(input) {
  const scope = {
    mcpOrganizationId: input.organizationId,
    wikiInstallationId: input.wikiInstallationId,
    documentOrganizationId: input.documentOrganizationId,
    authorAssignmentId: input.identity.assignmentId,
  };
  return `${input.instructions}\n\nTask scope:\n${JSON.stringify(scope)}\n\nUse mcpOrganizationId for MCP organizationId arguments and wikiInstallationId for Wiki installationId arguments. For generated documents include organization_id from documentOrganizationId; for plans include owner_assignment_id from authorAssignmentId. These references grant no write or approval authority.\n\n${input.mcp ? 'Call the native Outfitter MCP whoami tool once to verify your current acting principal. ' : ''}${completionInstructions}\n\nTask evidence:\n${JSON.stringify(input.evidence ?? {})}`;
}
