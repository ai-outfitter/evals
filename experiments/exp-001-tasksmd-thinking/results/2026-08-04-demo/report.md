# exp-001-tasksmd-thinking

> On the TASKS.md calculator task, does raising the thinking level improve outcomes for local models, and how do qwen3:32b, qwen3:4b, and nemotron-3-nano:4b compare when everything else is held constant?

## Cells

| cell | status | failure class | scores | wall clock (s) | tokens out |
| --- | --- | --- | --- | --- | --- |
| tasksmd-calculator-ollama-nemotron-3-nano-4b-high-worktree-implementer-r1 | fail | benchmark | tasks_completed=0/5, tests_pass=0/1 | 19 | 1580 |
| tasksmd-calculator-ollama-nemotron-3-nano-4b-low-worktree-implementer-r1 | fail | benchmark | tasks_completed=0/5, tests_pass=0/1 | 51 | 179 |
| tasksmd-calculator-ollama-nemotron-3-nano-4b-off-worktree-implementer-r1 | fail | benchmark | tasks_completed=0/5, tests_pass=0/1 | 15 | 952 |
| tasksmd-calculator-ollama-qwen3-4b-high-worktree-implementer-r1 | invalid | agent_execution | tasks_completed=0/5, tests_pass=0/1 | 481 | 0 |
| tasksmd-calculator-ollama-qwen3-4b-low-worktree-implementer-r1 | invalid | agent_execution | tasks_completed=0/5, tests_pass=0/1 | 481 | 0 |
| tasksmd-calculator-ollama-qwen3-4b-off-worktree-implementer-r1 | fail | benchmark | tasks_completed=0/5, tests_pass=0/1 | 510 | 4029 |

## Judge preferences

### by model

| model | diff_quality | instruction_adherence | overall | task_completion |
| --- | --- | --- | --- | --- |
| ollama/nemotron-3-nano:4b | — | — | — | — |
| ollama/qwen3:4b | — | — | — | — |

### by thinking

| thinking | diff_quality | instruction_adherence | overall | task_completion |
| --- | --- | --- | --- | --- |
| high | — | — | — | — |
| low | — | — | — | — |
| off | — | — | — | — |


## Human preferences

(no records)

## Judge vs human agreement

| judge | shared verdicts | matching | agreement |
| --- | --- | --- | --- |
| judge-nemotron | 0 | 0 | — |
| judge-qwen4b | 0 | 0 | — |
