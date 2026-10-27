#!/usr/bin/env bash
# Re-run edit only (raw frames + manifest already captured).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${DEMO_V2_OUT:-$(cd "$(dirname "$0")/.." && pwd)/out/demo}"
FRAME_DIR="${1:-}"
COUNT="${2:-}"
MANIFEST="${3:-$OUT/crop-manifest.json}"
WORK="${4:-$OUT/_work}"
if [[ -z "$FRAME_DIR" || -z "$COUNT" ]]; then
  echo "Usage: $0 <frameDir> <frameCount> [manifest] [workDir]" >&2
  exit 1
fi
export DEMO_V2_OUT="$OUT"
export DEMO_SRC_W="${DEMO_SRC_W:-1440}"
export DEMO_SRC_H="${DEMO_SRC_H:-900}"
export DEMO_DSF="${DEMO_DSF:-2}"
export DEMO_DURATION="${DEMO_DURATION:-14.5}"
exec node "$ROOT/tools/demo_edit.mjs" "$FRAME_DIR" "$COUNT" "$MANIFEST" "$WORK"
