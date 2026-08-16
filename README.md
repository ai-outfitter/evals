# evals

Declarative, container-based agent evals. An experiment file declares a matrix
of models × thinking levels × agents over a task; every cell runs an agent
(Pi inside a pinned container image, or `claude -p` on the host); outputs are
scored by a deterministic evaluator where one exists, compared pairwise by
blinded judge agents, and rated pairwise by a blinded human. Local runs emit
reproducible, attestable bundles; the same experiment file and image digest
are designed to replay in GitHub or Forgejo Actions with attestations.

Successor to the attested benchmark fixture (see
[Validated prior proof](#validated-prior-proof)).

This repository ships the harness and its test fixtures. Real experiments —
their tasks, rubrics, and recorded results — arrive in their own changes, one
per experiment, so the machinery can be reviewed apart from the findings.

## Runners

A provider declares how its cells execute:

| runner | how | isolation |
| --- | --- | --- |
| `pi-container` | Pi inside `localhost/evals-agent` (built by `nix build .#agent-image`) | containerized; observed digest recorded on every result |
| `claude-cli` | `claude -p --output-format json` on the host | **outside the container**; authenticates from the host session |

The `claude-cli` tradeoff is deliberate: provider diversity in exchange for
weaker isolation. It is recorded on every result as `variant.harness`, never
implied. Thinking level maps to `MAX_THINKING_TOKENS`; the prompt goes on
stdin because `--disallowedTools` takes a variadic list.

## Quick start

```bash
nix develop
nix build .#agent-image && podman load < result

evals validate tests/fixtures/exp-transform.yaml
evals smoke    tests/fixtures/exp-transform.yaml ollama/qwen3:0.6b

evals run    tests/fixtures/exp-transform.yaml --run-id demo
evals judge  tests/fixtures/exp-transform.yaml --run-id demo
evals prefer tests/fixtures/exp-transform.yaml --run-id demo
evals reduce tests/fixtures/exp-transform.yaml --run-id demo
evals bundle tests/fixtures/exp-transform.yaml --run-id demo --validate

# <result.json> is one agent's classifier-result/v1 JSON; <expected.json> is
# the hidden label kept out of the prompt (tasks/<task-id>/expected/<case>.json).
evals score-classifier <result.json> <expected.json> --k 20
```

The report lands at `runs/<experiment>/<run-id>/report.md`: per-cell status and
scores, judge and human win rates per metric per matrix axis, and
judge-vs-human agreement.

## The declarative format

- `experiments/<id>/experiment.yaml` — the unit of a run and of
  preregistration: research question, independent variables, container image +
  digest, matrix, task reference, timeouts, judges, judging inputs, human
  metrics, provenance requirements. Immutable during a run; results never
  write back into it.
- `tasks/<id>/task.yaml` — a reusable task: `kind` (`worktree-*` kinds get a
  fresh git fixture and tools; `transform-*` kinds get an input inlined into
  the prompt and no tools), prompt, optional deterministic evaluator, metrics.
  A `transform-classifier` task also names a hidden expected-label file.
- `schemas/classifier-result.schema.json` — the strict boolean and ranking
  result contract. `schemas/classifier-expected.schema.json` defines hidden labels.
- `judges/*.yaml` — rubrics: versioned metric definitions plus judging
  instructions.
- `models.yaml` — provider registry (Ollama, GitHub Models); rendered into a
  Pi `models.json` per cell.

Cells are identified by a deterministic `cell_id` plus a `spec_hash` over the
resolved cell spec. Completed cells are skipped on re-run (`--rerun`
overrides), so a run is resumable at every stage.

## Blinding

Judges and the human see candidates only as "Response A/B", assignment
randomized per comparison with the seed recorded in the verdict. Comparison
inputs are the diff, final message, output, and evaluator summary — never
session metadata, which contains model names. Pairs differ in exactly one
independent variable.

## Provenance

`evals bundle` writes byte-reproducible tars per cell plus a run manifest that
includes the experiment file digest. Bundles are unattested locally; a hosted
replay of the same experiment file and image digest attests the same byte
format via the credential-separated pipeline described in
[`docs/design.md`](docs/design.md) and [`docs/actions-replay.md`](docs/actions-replay.md).

> **Claim boundary:** single-repetition runs and the committed demo
> experiments are development evidence, not benchmark results. Comparative
> claims require preregistered tasks, repeated runs, locked settings, and
> uncertainty estimates.

## Repository layout

```text
experiments/        Experiment declarations (one directory per experiment)
tasks/              Reusable tasks: fixture/input, prompt, evaluator
judges/             Judge rubrics
tests/fixtures/     Harness test fixtures: a worktree and a transform experiment
schemas/            JSON Schemas: experiment, task, result, verdict, preference
src/evals/          The harness (evals CLI)
src/evals/schemas/  Copy of schemas/ shipped in the wheel (checked for drift)
models.yaml         Provider registry
flake.nix           Dev shell + agent container image (nix build .#agent-image)
runs/               Result store (gitignored)
scripts/check.sh    Full offline check: schemas, tests, stub pipeline
docs/               Design, security, portability, legacy workflow
reports/evals/      Committed attested reports from the prior fixture harness
```

## Validated prior proof

The v1 fixture harness proved the hosted attestation chain end to end: run
[29785706884](https://github.com/ai-outfitter/benchmarks/actions/runs/29785706884)
committed an independently verifiable report at
[`reports/evals/29785706884/`](reports/evals/29785706884/).

```bash
gh attestation verify reports/evals/29785706884/report.tar.gz \
  --repo ai-outfitter/benchmarks \
  --predicate-type https://ai-outfitter.dev/attestations/benchmark-report/v1
```

Its workflow and profile are archived under [`docs/legacy/`](docs/legacy/);
its fixture lives on as `tasks/infra-fixture-001`.

## Roadmap

1. Generic Actions workflow that expands `strategy.matrix` from
   `evals matrix --github-json` and attests cell bundles.
2. Publish the agent image to GHCR and pin experiments by registry digest.
3. Forgejo replay on a self-hosted runner that reaches local Ollama
   ([`docs/forgejo.md`](docs/forgejo.md)).
4. Repetitions and uncertainty before any comparative claim.
5. Further task kinds: transcription+diarization comparison, CAD generation,
   multi-agent forge cycles.

## License

MIT — see [`LICENSE.md`](LICENSE.md).
