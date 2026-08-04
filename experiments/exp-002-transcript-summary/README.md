# exp-002 — transcript summarizer

**Question.** Which model produces the best structured summary of a raw
voice-memo transcript, does thinking level change summary quality, and how far
behind a hosted baseline (Claude Haiku) are the local models?

**Shape.** `transform-judge`: the input is inlined into the prompt, the agent
has no tools, and there is **no deterministic evaluator** — quality here is not
mechanically checkable, so every cell is `unscored` and ranking comes entirely
from blinded pairwise comparison. This is the path that exists for tasks where
only judgement can decide.

## The task

`tasks/transcript-summary/input/transcript.txt` is an unedited speech-to-text
transcript of a spoken voice memo about agent personas — disfluent, with ASR
errors left in (`A_I_ Outfitter`, `Weyland, Utah`). The prompt asks for clean
Markdown under three fixed headings, and forbids inventing claims.

Metrics: **fidelity** (no unsupported claims), **coverage** (the distinct
points survive), **concision**.

## What happened

Read [`results/2026-08-04-demo/`](results/2026-08-04-demo/) — `report.md` for
the tables, `cells/<cell>/output.md` for the eight summaries themselves, and
`judgments/<judge>/` for every verdict with its blinding seed.

Eight cells, three judges, 45 valid verdicts:

| model | coverage | fidelity | concision | overall | wall clock |
| --- | --- | --- | --- | --- | --- |
| claude-cli/haiku | **12/12 (100%)** | 2/5 (40%) | 6/12 (50%) | **8/9 (89%)** | ~10 s |
| ollama/qwen3:4b | 3/11 (27%) | **6/7 (86%)** | 9/14 (64%) | 9/13 (69%) | 14–37 s |
| ollama/qwen3:32b | 5/10 (50%) | 4/8 (50%) | 3/12 (25%) | 5/12 (42%) | 290–498 s |
| ollama/nemotron-3-nano:4b | 3/13 (23%) | 2/8 (25%) | 7/12 (58%) | 1/12 (8%) | 4–5 s |

Haiku wins coverage unanimously and overall at 89%, in about ten seconds —
against qwen3:32b's five to eight minutes. The metrics genuinely trade off
against each other: the tightest summaries are not the most complete ones.

## The bug this experiment found in itself

The first judging pass scored **fidelity without ever showing judges the source
transcript**. The three local judges scored it anyway. The Haiku judge refused,
replying "I need to read the transcript to evaluate these summaries" — 14 of its
16 verdicts were invalid because it narrated instead of returning a verdict.

That refusal was correct, and it exposed a real defect: fidelity had been
unjudgeable all along, and the local judges' confident numbers were noise. The
judge prompt now includes the task's source material; Haiku's invalid rate fell
to 1 in 16, and **every earlier verdict was discarded and recomputed**. The
table above is from the corrected pass.

The lesson worth keeping: a judge that fails loudly is more useful than judges
that guess quietly. A judge whose invalid rate spikes is evidence about the
*harness*, not only about the judge.

## What this does and does not show

It shows the judge-only path works end to end, and that a hosted small model
beats this local pool on coverage by a wide margin at a fraction of the wall
clock.

It does not establish a best model. One transcript, one repetition, at most a
handful of decided comparisons per cell — a single flipped verdict moves those
percentages by 12 to 25 points. And **every judge favours its own family**:
the Haiku judge ranks Haiku first, the qwen3:4b judge ranks qwen3:4b first and
Haiku third. Read the per-judge split before trusting any aggregate.

The human preference pass has not been recorded, so the judge-versus-human
agreement column in `report.md` is empty. That column is the point of the
exercise; until it is filled, the judges are unvalidated.

## Reproducing

```bash
nix build .#agent-image && podman load < result
evals run    experiments/exp-002-transcript-summary/experiment.yaml --run-id demo
evals judge  experiments/exp-002-transcript-summary/experiment.yaml --run-id demo
evals prefer experiments/exp-002-transcript-summary/experiment.yaml --run-id demo
evals reduce experiments/exp-002-transcript-summary/experiment.yaml --run-id demo
```

Development evidence, not a benchmark result.
