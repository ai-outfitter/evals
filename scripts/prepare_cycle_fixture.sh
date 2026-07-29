#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 FIXTURE_REPO WORKDIR" >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_repo="$(realpath "$1")"
workdir="$2"

rm -rf "$workdir"
mkdir -p "$workdir"
workdir="$(realpath "$workdir")"

git init -q --bare -b main "$workdir/origin.git"

seed="$workdir/seed"
mkdir -p "$seed"
cp -a "$fixture_repo/." "$seed/"
git -C "$seed" init -q -b main
git -C "$seed" config user.name "benchmark-fixture"
git -C "$seed" config user.email "benchmark-fixture@example.invalid"
git -C "$seed" add .
git -C "$seed" commit -qm "fixture baseline"
git -C "$seed" remote add origin "$workdir/origin.git"
git -C "$seed" push -q origin main
rm -rf "$seed"

# Branch protection (pre-receive hook) activates with forge state, after the
# baseline push above.
python3 "$root/fixtures/task-002/forge/forge.py" \
  --state "$workdir/forge" init \
  --git-dir "$workdir/origin.git" \
  --default-branch main >/dev/null

echo "$workdir"
