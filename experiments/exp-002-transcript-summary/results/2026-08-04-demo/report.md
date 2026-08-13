# exp-002-transcript-summary

> Which model produces the best structured summary of a raw voice-memo transcript, does thinking level change summary quality, and how far behind a hosted baseline (Claude Haiku) are the local models?

## Cells

| cell | status | failure class | scores | wall clock (s) | tokens out |
| --- | --- | --- | --- | --- | --- |
| transcript-summary-claude-cli-haiku-high-summarizer-r1 | unscored | none | — | 10 | 746 |
| transcript-summary-claude-cli-haiku-off-summarizer-r1 | unscored | none | — | 10 | 785 |
| transcript-summary-ollama-nemotron-3-nano-4b-high-summarizer-r1 | unscored | none | — | 4 | 323 |
| transcript-summary-ollama-nemotron-3-nano-4b-off-summarizer-r1 | unscored | none | — | 5 | 393 |
| transcript-summary-ollama-qwen3-32b-high-summarizer-r1 | unscored | none | — | 290 | 433 |
| transcript-summary-ollama-qwen3-32b-off-summarizer-r1 | unscored | none | — | 498 | 666 |
| transcript-summary-ollama-qwen3-4b-high-summarizer-r1 | unscored | none | — | 14 | 1294 |
| transcript-summary-ollama-qwen3-4b-off-summarizer-r1 | unscored | none | — | 37 | 2916 |

## Judge preferences

### by model

| model | concision | coverage | fidelity | overall |
| --- | --- | --- | --- | --- |
| claude-cli/haiku | 6/12 (50%) | 12/12 (100%) | 2/5 (40%) | 8/9 (89%) |
| ollama/nemotron-3-nano:4b | 7/12 (58%) | 3/13 (23%) | 2/8 (25%) | 1/12 (8%) |
| ollama/qwen3:32b | 3/12 (25%) | 5/10 (50%) | 4/8 (50%) | 5/12 (42%) |
| ollama/qwen3:4b | 9/14 (64%) | 3/11 (27%) | 6/7 (86%) | 9/13 (69%) |

### by thinking

| thinking | concision | coverage | fidelity | overall |
| --- | --- | --- | --- | --- |
| high | 5/6 (83%) | 2/5 (40%) | 3/7 (43%) | 5/9 (56%) |
| off | 1/6 (17%) | 3/5 (60%) | 4/7 (57%) | 4/9 (44%) |


## Human preferences

(no records)

## Judge vs human agreement

| judge | shared verdicts | matching | agreement |
| --- | --- | --- | --- |
| judge-haiku | 0 | 0 | — |
| judge-nemotron | 0 | 0 | — |
| judge-qwen4b | 0 | 0 | — |
