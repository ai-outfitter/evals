# exp-001 — TASKS.md worktree agent

**Question.** On a five-item `TASKS.md`, does raising the thinking level improve
outcomes for local models, and how do they compare when everything else is held
constant?

**Shape.** `worktree-tasksmd`: the agent gets a fresh git worktree containing a
small calculator repo and a `TASKS.md` work queue, and has tools. A held-out
evaluator scores the result — the agent never sees it.

## The task

`tasks/tasksmd-calculator/repo/TASKS.md` lists five items, chosen so partial
credit is measurable rather than pass/fail:

1. Fix `subtract` — it returns `b - a`.
2. Implement `multiply`.
3. Implement `divide`, raising `ValueError` on divide-by-zero.
4. Add boolean rejection to all four operations.
5. Document all four operations under a `## Usage` heading in `README.md`.

The evaluator (`tasks/tasksmd-calculator/evaluator/evaluate.py`) checks each
item independently and runs the public test suite, emitting
`tasks_completed: n/5` and `tests_pass: 0|1`.

## What happened

Read the recorded run in [`results/2026-08-04-demo/`](results/2026-08-04-demo/)
— `report.md` for the table, `cells/<cell>/patch.diff` for what each agent
actually wrote.

**Every completed local-model cell scored 0/5 with an empty diff.** This is a
floor result: the task did not discriminate between models because none of them
could do it at all.

| cell | status | tasks | wall clock |
| --- | --- | --- | --- |
| nemotron-3-nano:4b · off | fail | 0/5 | 15 s |
| nemotron-3-nano:4b · low | fail | 0/5 | 51 s |
| nemotron-3-nano:4b · high | fail | 0/5 | 19 s |
| qwen3:4b · off | fail | 0/5 | 510 s |
| qwen3:4b · low | invalid (timeout) | — | 481 s |
| qwen3:4b · high | invalid (timeout) | — | 481 s |

The failure modes differ in a way worth keeping:

- **nemotron** called `read` two or three times, then stopped and answered in
  prose. It failed fast — 15 to 51 seconds.
- **qwen3:4b** spent its entire budget thinking. In the `off` cell it read six
  files over 510 seconds and still wrote nothing; `low` and `high` hit the
  480-second timeout and are recorded `invalid` / `agent_execution`, not as
  losses.

Judges tied every pair, which is the correct verdict on two empty diffs.

## What this does and does not show

It shows that this pool of 4b-class local models cannot drive a multi-step
tool-editing loop, consistent with an earlier local-model spike where a
read-then-edit sequence took roughly 24 minutes on a larger local model.

It shows nothing about the relative ability of these models, because a task at
floor has no variance to measure. It also cannot speak to thinking level: qwen3
emits thinking blocks even at `thinking: off` over this API
(`supportsReasoningEffort: false`), so the axis compares *requested* levels, not
actual reasoning budgets.

**Not yet run:** the qwen3:32b cells (roughly 40 minutes each) and the
`claude-cli/haiku` cells. Until a contestant clears the floor, this experiment
has no ranking to report.

## Reproducing

```bash
nix build .#agent-image && podman load < result
evals run    experiments/exp-001-tasksmd-thinking/experiment.yaml --run-id demo
evals judge  experiments/exp-001-tasksmd-thinking/experiment.yaml --run-id demo
evals prefer experiments/exp-001-tasksmd-thinking/experiment.yaml --run-id demo
evals reduce experiments/exp-001-tasksmd-thinking/experiment.yaml --run-id demo
```

Single repetition, one task, no uncertainty estimate: development evidence, not
a benchmark result.
