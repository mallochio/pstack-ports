#!/usr/bin/env bash
# Install the pstack opencode port into an opencode config dir.
set -euo pipefail
DST="${1:-$HOME/.config/opencode}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SRC/scripts/install_tree.py" "$SRC" "$DST" skills agents commands LICENSE-cursor-team-kit
