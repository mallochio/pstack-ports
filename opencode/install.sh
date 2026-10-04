#!/usr/bin/env bash
# Install the pstack opencode port into an opencode config dir.
set -euo pipefail
DST="${1:-$HOME/.config/opencode}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$DST/skills" "$DST/agents" "$DST/commands"
cp -R "$SRC/skills/." "$DST/skills/"
cp "$SRC/agents/"*.md "$DST/agents/"
cp "$SRC/commands/"*.md "$DST/commands/"
echo "Installed pstack skills to $DST/skills, agents to $DST/agents, commands to $DST/commands"
echo "Optional: run /setup-pstack inside opencode to configure per-role models."
