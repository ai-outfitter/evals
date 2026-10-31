// These evaluations submit ordinary A2A tasks, without a workflow manifest.
// Workflow output tools cannot operate on them; persistence is handled outside
// the model session by the authenticated adapter.
export function selectResidentTaskTools(tools) {
  const allowed = new Set(['a2a_read_task', 'a2a_complete_task']);
  return tools.filter(tool => allowed.has(tool.name));
}

export const completionInstructions = 'Complete this ordinary A2A task with a2a_complete_task: set outcome to "completed" and response to a JSON string containing the requested object. Do not supply outputs or call workflow output tools. Do not approve/adopt documents.';
