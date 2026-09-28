#!/usr/bin/env bash
# Start Sanctum the way the privileged capabilities need: a root helper on a
# 0600 socket, then the desktop window as yourself. See docs/user-manual.md §3.
#
# It asks for your sudo password once, in this terminal. Only the helper runs
# as root; the window and the API never do. Ctrl+C stops both.
#
# State stays in ~/.local/share/sanctum (override with SANCTUM_STATE_DIR) so
# the cases and ledger you already have are the ones you see.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
STATE="${SANCTUM_STATE_DIR:-$HOME/.local/share/sanctum}"
SOCKET="${SANCTUM_HELPER_SOCKET:-/run/sanctum/helper.sock}"

[ -x "$PY" ] || { echo "No interpreter at $PY. Run 'make install' first." >&2; exit 1; }
mkdir -p "$STATE"

sudo -v
sudo "$PY" -m helper --operator-uid "$(id -u)" --state-dir "$STATE" --socket "$SOCKET" &
HELPER=$!
trap 'sudo kill "$HELPER" 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  [ -S "$SOCKET" ] && break
  sleep 0.2
done
[ -S "$SOCKET" ] || { echo "The helper did not create $SOCKET." >&2; exit 1; }

cd "$ROOT"
SANCTUM_HELPER_SOCKET="$SOCKET" SANCTUM_STATE_DIR="$STATE" "$PY" -m api.desktop
