#!/usr/bin/env bash
# Verify the installed copy is serving this checkout, rather than an older
# process which happened to own REVAMP_PORT.  Intended to run after installation.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
port=${REVAMP_PORT:-8114}
source_version=$(sed -nE 's/^VERSION = "([^"]+)"/\1/p' "$root/revamp_core/server.py")
test -n "$source_version"

health=$(curl --fail --silent --show-error "http://127.0.0.1:${port}/api/health")
served_version=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))' <<<"$health")
served_build=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("build", ""))' <<<"$health")
test "$served_version" = "$source_version" || {
  printf 'stale server: source version %s, health version %s\n' "$source_version" "$served_version" >&2; exit 1;
}
test -n "$served_build" || { echo 'health did not include a build fingerprint' >&2; exit 1; }

# ss's first column is one line per listener.  A wildcard listener is still a
# single process, but duplicate PID rows are not.
listeners=$(ss -ltnp "sport = :${port}" 2>/dev/null | awk 'NR>1 {print}' || true)
count=$(printf '%s\n' "$listeners" | sed '/^$/d' | wc -l | tr -d ' ')
test "$count" = 1 || {
  printf 'expected exactly one listener on REVAMP_PORT=%s, found %s:\n%s\n' "$port" "$count" "$listeners" >&2; exit 1;
}
printf 'install verified: v%s build %s, one listener on %s\n' "$served_version" "$served_build" "$port"
