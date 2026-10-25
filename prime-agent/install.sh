#!/usr/bin/env bash
# Install the pstack prime-agent port into a prime-agent agent dir.
set -euo pipefail
DST="${1:-$HOME/.prime/agent}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SRC/scripts/install_tree.py" "$SRC" "$DST" skills agents prompts LICENSE-cursor-team-kit
