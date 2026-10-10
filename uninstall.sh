#!/usr/bin/env bash
# Remove the pstack ports from prime-agent and opencode.
#
#   ./uninstall.sh             # remove from both harnesses
#   ./uninstall.sh prime       # prime-agent only  (default ~/.prime/agent)
#   ./uninstall.sh opencode    # opencode only     (default ~/.config/opencode)
#
# Removes only the entries this repo installs — the skills/, agents/, and
# prompts/ (prime) or commands/ (opencode) names present in the port tree —
# plus the artifacts /setup-pstack writes:
#   prime:    the pstack-models block in AGENTS.md (agent dir and its parent)
#   opencode: pstack-models.md and its entry in opencode.json `instructions`
# Anything you added yourself under those dirs is left alone.
#
# Destination overrides: PRIME_DST=..., OPENCODE_DST=...
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

target="${1:-all}"
case "$target" in
  all|prime|opencode) ;;
  -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
  *) echo "usage: $0 [prime|opencode]" >&2; exit 2 ;;
esac

# Remove, under $2, every top-level name present under $1.
remove_tree_entries() {
  [ -d "$1" ] || return 0
  local entry
  for entry in "$1"/*; do
    rm -rf "$2/$(basename "$entry")"
  done
}

# Delete the <!-- pstack-models:start --> ... :end --> block from $1.
# Removes the file entirely if the block was all it contained.
strip_models_block() {
  local f="$1" tmp
  { [ -f "$f" ] && grep -q 'pstack-models:start' "$f"; } || return 0
  tmp="$f.pstack-uninstall"
  sed '/<!-- pstack-models:start -->/,/<!-- pstack-models:end -->/d' "$f" > "$tmp"
  if [ -z "$(tr -d '[:space:]' < "$tmp")" ]; then
    rm -f "$tmp" "$f"
  else
    mv "$tmp" "$f"
  fi
}

uninstall_prime() {
  local dst="$1" src="$ROOT/prime-agent"
  remove_tree_entries "$src/skills"  "$dst/skills"
  remove_tree_entries "$src/agents"  "$dst/agents"
  remove_tree_entries "$src/prompts" "$dst/prompts"
  strip_models_block "$dst/AGENTS.md"
  strip_models_block "$(dirname "$dst")/AGENTS.md"
  if [ -f "$dst/settings.json" ] && grep -q pstack "$dst/settings.json"; then
    echo "note: $dst/settings.json still references pstack" \
      "(a 'packages' entry?) — remove it by hand" >&2
  fi
  echo "Removed pstack from $dst"
}

uninstall_opencode() {
  local dst="$1" src="$ROOT/opencode"
  remove_tree_entries "$src/skills"   "$dst/skills"
  remove_tree_entries "$src/agents"   "$dst/agents"
  remove_tree_entries "$src/commands" "$dst/commands"
  rm -f "$dst/pstack-models.md"
  if [ -f "$dst/opencode.json" ] && grep -q 'pstack-models' "$dst/opencode.json"; then
    DST="$dst" python3 - <<'PY'
import json, os, re
path = os.path.join(os.environ["DST"], "opencode.json")
text = open(path).read()
kept = [i for i in json.loads(text).get("instructions") or []
        if "pstack-models" not in i]
if kept:
    # replace just the array value, leaving all other formatting alone
    text = re.sub(r'"instructions"\s*:\s*\[[^]]*\]',
                  '"instructions": ' + json.dumps(kept), text, count=1)
else:
    # drop the whole key line, comma included
    text = re.sub(r'[ \t]*"instructions"[^]]*\],?\n', "", text, count=1)
json.loads(text)
open(path, "w").write(text)
PY
  fi
  echo "Removed pstack from $dst"
}

if [ "$target" = all ] || [ "$target" = prime ]; then
  uninstall_prime "${PRIME_DST:-$HOME/.prime/agent}"
fi
if [ "$target" = all ] || [ "$target" = opencode ]; then
  uninstall_opencode "${OPENCODE_DST:-$HOME/.config/opencode}"
fi
