#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
check_root=$(mktemp -d "${TMPDIR:-/tmp}/revamp-fresh.XXXXXX")
server_pid=""

cleanup() {
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid"
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$check_root"
}
trap cleanup EXIT INT TERM

port=$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)

plugin_home="$check_root/hermes/plugins/revamp"
mkdir -p "$plugin_home"
cp -R "$repo_root"/. "$plugin_home"
rm -rf "$plugin_home/.git" "$plugin_home/lanes"

HERMES_HOME="$check_root/hermes" \
REVAMP_HOME="$check_root/hermes/revamp" \
REVAMP_PORT="$port" \
PYTHONPATH="$plugin_home" \
python3 -m revamp_core.server --port "$port" --data "$check_root/hermes/revamp" >"$check_root/server.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 50); do
  if curl --fail --silent "http://127.0.0.1:$port/api/health" >"$check_root/health.json"; then
    break
  fi
  sleep 0.1
done

grep -q '"app": "revamp"' "$check_root/health.json"
token=$(tr -d '\n' < "$check_root/hermes/revamp/token")
test -n "$token"
status=$(curl --silent --output "$check_root/state.json" --write-out '%{http_code}' \
  -H "X-Revamp-Token: $token" "http://127.0.0.1:$port/api/state")
test "$status" = "200"
grep -q '"volume"' "$check_root/state.json"
printf 'fresh install check passed: health and token-authenticated state on port %s\n' "$port"
