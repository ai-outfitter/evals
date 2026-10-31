#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m py_compile src/evals/*.py tests/*.py tasks/*/evaluator/evaluate.py
bash -n scripts/*.sh
if compgen -G ".github/workflows/*.yml" > /dev/null; then
  actionlint .github/workflows/*.yml
fi

# Spec validation for the test fixtures and every committed experiment.
for experiment in tests/fixtures/exp-*.yaml experiments/*/experiment.yaml; do
  [[ -f "$experiment" ]] || continue
  python3 -m evals.cli validate "$experiment"
done
for experiment in experiments/*/resident.yaml; do
  [[ -f "$experiment" ]] || continue
  python3 -m evals.cli resident validate "$experiment"
done

# Unit tests include the full stub pipeline:
# run -> resume -> judge -> prefer(auto) -> reduce -> bundle -> validate.
python3 -m unittest discover -s tests -v
node --test tests/resident-mcp-relay.test.mjs
node --check scripts/resident-task.mjs
node --check scripts/resident-mcp.mjs

if git check-ignore --no-index -q reports/evals/test/report.tar.gz; then
  echo "committed report subjects must not be ignored" >&2
  exit 1
fi

# Fixture handoff regression: a patch captured from one fresh worktree applies
# cleanly to another (the agent/score credential-separation seam).
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"; find "$root" -type d -name __pycache__ -prune -exec rm -rf {} +' EXIT
scripts/prepare_fixture.sh tasks/infra-fixture-001/repo "$tmp/worktree"
printf '\n# handoff check\n' >> "$tmp/worktree/calculator.py"
git -C "$tmp/worktree" diff --binary HEAD -- > "$tmp/patch.diff"
scripts/prepare_fixture.sh tasks/infra-fixture-001/repo "$tmp/score-worktree"
git -C "$tmp/score-worktree" apply --binary "$tmp/patch.diff"
grep -q 'handoff check' "$tmp/score-worktree/calculator.py"

echo "all checks passed"
