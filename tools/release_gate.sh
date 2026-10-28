#!/usr/bin/env bash
# A reproducible, local release gate. It makes its own disk-backed scratch area
# so browser profiles and clean exports never consume /tmp (often tmpfs).
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
scratch="${HOME}/.cache/revamp-gate"
visual=0
[[ ${1:-} == --visual ]] && visual=1
mkdir -p "$scratch"
export TMPDIR="$scratch/tmp"
mkdir -p "$TMPDIR"

declare -a names=() results=()
run() { local name=$1; shift; names+=("$name"); if "$@"; then results+=(PASS); else results+=(FAIL); fi; }
run_shell() { local name=$1 command=$2; names+=("$name"); if (cd "$root" && bash -o pipefail -c "$command"); then results+=(PASS); else results+=(FAIL); fi; }

run_shell "pytest" '
  for t in tests/test_*.py; do
    stem=${t##*/}; stem=${stem%.py}
    python3 -m pytest -q -p no:cacheprovider --basetemp="$TMPDIR/$stem" "$t" || exit $?
  done'
run_shell "node tests" 'for t in tests/*.test.mjs; do node --test "$t"; done'
run_shell "browser e2e" 'python3 -m pytest -q -p no:cacheprovider tests/test_*e2e.py'

export_dir=$(mktemp -d "$scratch/export.XXXXXX")
export export_dir
trap 'rm -rf "$export_dir"' EXIT
names+=("clean export")
if cp -a "$root/." "$export_dir/" && rm -rf "$export_dir/.git" "$export_dir/lanes"; then results+=(PASS); else results+=(FAIL); fi
run_shell "Hermes manifest" 'hermes plugins validate "$export_dir"'
run_shell "private-info scan" '
  private_home="/home""/dev"; private_mount="/mnt""/homes"
  private_net_a="10""\\.0\\.0\\."; private_net_b="192""\\.168\\."
  private_names="\\b(callisto""-odin|[A-Za-z0-9._-]+\\.local)\\b"
  ! rg -n -e "$private_home" -e "$private_mount" -e "$private_net_a" -e "$private_net_b" -e "$private_names" "$export_dir" --glob "!*.pyc" --glob "!*.png" --glob "!*.jpg"'
run_shell "version consistency" '
  v=$(sed -nE "s/^version: *([^ ]+).*/\\1/p" plugin.yaml); \
  test "$v" = "$(sed -nE "s/^VERSION = \\\"([^\\\"]+)\\\"/\\1/p" revamp_core/server.py)"; \
  grep -Fq "${v#1.0.0-}" web/index.html; \
  grep -Fq "\"version\": \"$v\"" claude-code/revamp/.claude-plugin/plugin.json; \
  grep -Fq "## $v " CHANGELOG.md'
if (( visual )); then run_shell "visual sweep" 'node tools/visual_sweep.mjs'; fi

printf '\n%-24s %s\n' CHECK RESULT
failed=0
for i in "${!names[@]}"; do printf '%-24s %s\n' "${names[i]}" "${results[i]}"; [[ ${results[i]} == PASS ]] || failed=1; done
exit "$failed"
