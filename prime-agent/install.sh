#!/usr/bin/env bash
# Install the pstack prime-agent port into a prime-agent agent dir.
set -euo pipefail
DST="${1:-$HOME/.prime/agent}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$DST/skills" "$DST/agents" "$DST/prompts"
cp -R "$SRC/skills/." "$DST/skills/"
cp "$SRC/agents/"*.md "$DST/agents/"
cp "$SRC/prompts/"*.md "$DST/prompts/" 2>/dev/null || true
echo "Installed pstack skills to $DST/skills, agent specs to $DST/agents, prompts to $DST/prompts"
echo "Alternative: add this directory to the packages array in $DST/settings.json instead of copying."
echo "Optional: run /skill:setup-pstack inside prime-agent to configure per-role models."
