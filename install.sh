#!/usr/bin/env bash
# Install the pstack ports into prime-agent and opencode in one go.
#
#   ./install.sh             # install into both harnesses
#   ./install.sh prime       # prime-agent only  (default ~/.prime/agent)
#   ./install.sh opencode    # opencode only     (default ~/.config/opencode)
#
# Destination overrides:
#   PRIME_DST=/path ./install.sh prime
#   OPENCODE_DST=/path ./install.sh opencode
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

target="${1:-all}"
case "$target" in
  all|prime|opencode) ;;
  -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
  *) echo "usage: $0 [prime|opencode]" >&2; exit 2 ;;
esac

if [ "$target" = all ] || [ "$target" = prime ]; then
  "$ROOT/prime-agent/install.sh" "${PRIME_DST:-$HOME/.prime/agent}"
fi
if [ "$target" = all ] || [ "$target" = opencode ]; then
  "$ROOT/opencode/install.sh" "${OPENCODE_DST:-$HOME/.config/opencode}"
fi
