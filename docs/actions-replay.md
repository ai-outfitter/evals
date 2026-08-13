# Actions replay seam

A local run and a hosted run consume the same `experiment.yaml`. This
document is the design for the hosted half; only `evals matrix --github-json`
is implemented so far.

## Workflow shape

The generic workflow replaces the v1 hand-written matrix
(`docs/legacy/fixture-eval.yml`) with one expanded from the experiment file:

1. **preflight** — `evals validate`, then
   `evals matrix --github-json experiments/<id>/experiment.yaml` into a job
   output consumed by `fromJSON` in `strategy.matrix`.
2. **agent (matrix)** — one job per cell. Pulls the experiment's
   `container.image@digest`, runs the cell exactly as
   `evals run --filter cell=<cell_id>` does locally, uploads the cell
   directory as an artifact. Model credentials only; no attestation authority.
3. **score** — for evaluator tasks, applies the patch to a fresh worktree and
   runs the evaluator in the digest-pinned hardened container
   (`--network none --read-only --cap-drop ALL`, as in the v1 pipeline).
4. **attest (matrix)** — `evals bundle --validate` on the inert cell bundle,
   then `actions/attest` with the cell's `attestation-predicate.json` fields
   as a custom predicate. First job with `id-token`/`attestations` permissions.
5. **judge** — judge cells are ordinary matrix cells (model credentials, no
   write); verdict records are attested like results.
6. **reduce / publish** — verify every expected cell attestation
   (`gh attestation verify`), `evals reduce`, attest the report, optionally
   commit under `reports/evals/<run-id>/` with a separate `contents: write`
   job. Human preferences cannot be collected hosted; the reducer reports
   judge-only tallies and marks the human column absent.

## The digest bridge

`container.digest` in the experiment file is the local↔hosted contract: both
sides run the identical image, and every result records the observed digest.
Locally built images carry a nix image id; before a hosted replay the image is
pushed to GHCR and the experiment file is updated to the registry digest —
that edit changes every cell's `spec_hash`, which is the point: a replayed
cell either matches the preregistered spec or is visibly a different
experiment.

Bundles are byte-reproducible (sorted tar, zeroed mtimes, `gzip -n`
equivalent), so the only difference between a local bundle and a hosted one is
the attestation signature.

## Model reachability tiers

| run.origin | runner | reachable providers |
| --- | --- | --- |
| `local` | this machine | Ollama on the LAN, cloud with keys |
| `github` | hosted runner | GitHub Models (`models: read`), cloud with secrets |
| `forgejo` | self-hosted runner on ocean | workstation Ollama **and** cloud — the interesting replay target for local-model cells |

The Forgejo half (machine account, report token scoping, portable in-toto
provenance instead of GitHub-native attestations) is unchanged from
[`forgejo.md`](forgejo.md): the machine-account token is authorization, not
attestation.
