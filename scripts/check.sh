#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python3 -m py_compile scripts/*.py \
  fixtures/task-001/evaluator/evaluate.py \
  fixtures/task-002/evaluator/evaluate.py \
  fixtures/task-002/forge/forge.py \
  tests/*.py
bash -n scripts/*.sh
actionlint .github/workflows/*.yml
python3 -m unittest discover -s tests -v

if git check-ignore --no-index -q reports/evals/test/report.tar.gz; then
  echo "committed report subjects must not be ignored" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"; find "$root" -type d -name __pycache__ -prune -exec rm -rf {} +' EXIT

scripts/prepare_fixture.sh fixtures/task-001/repo "$tmp/worktree"
mkdir -p "$tmp/candidate"
jq -n --arg agent_status success --arg infrastructure_error "" \
  '{agent_status: $agent_status, infrastructure_error: (if ($infrastructure_error | length) > 0 then $infrastructure_error else null end)}' \
  > "$tmp/candidate/execution.json"
test "$(jq -r .agent_status "$tmp/candidate/execution.json")" = success
printf '\n# handoff check\n' >> "$tmp/worktree/calculator.py"
git -C "$tmp/worktree" diff --binary HEAD -- > "$tmp/candidate/patch.diff"
scripts/prepare_fixture.sh fixtures/task-001/repo "$tmp/score-worktree"
git -C "$tmp/score-worktree" apply --binary "$tmp/candidate/patch.diff"
grep -q 'handoff check' "$tmp/score-worktree/calculator.py"
# Restore the baseline fixture for the collector's expected failing result.
scripts/prepare_fixture.sh fixtures/task-001/repo "$tmp/worktree"
printf '{"node":"test","outfitter":null,"pi":"test","harness":"base-pi"}\n' > "$tmp/runtime.json"
python3 scripts/collect_result.py \
  --worktree "$tmp/worktree" \
  --evaluator fixtures/task-001/evaluator/evaluate.py \
  --out "$tmp/results/base-pi" \
  --harness base-pi \
  --agent-status success \
  --task-id task-001 \
  --model gpt-4.1-mini \
  --provider github-models-legacy \
  --profile base \
  --runtime-metadata "$tmp/runtime.json" \
  >/dev/null

check-jsonschema \
  --schemafile schemas/result.schema.json \
  "$tmp/results/base-pi/result.json"

tar --sort=name --mtime='UTC 1970-01-01' \
  --owner=0 --group=0 --numeric-owner \
  -C "$tmp/results/base-pi" -cf - . | gzip -n > "$tmp/result-base-pi.tar.gz"
python3 scripts/validate_bundle.py \
  --bundle "$tmp/result-base-pi.tar.gz" \
  --extract-to "$tmp/validated" \
  --cell-id base-pi-task-001-r1 \
  --harness base-pi \
  --model gpt-4.1-mini \
  --provider github-models-legacy \
  --source-sha local \
  >/dev/null

python3 scripts/reduce_results.py "$tmp/results" "$tmp/report" >/dev/null
test -s "$tmp/report/report.md"
test -s "$tmp/report/results.json"

# task-002 full-cycle fixture: scripted reference run of both roles plus the
# evaluator, without model calls.
python3 scripts/run_cycle_reference.py --workdir "$tmp/cycle" >/dev/null

echo "all checks passed"
