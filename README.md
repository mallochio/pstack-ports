# pstack ports: opencode + prime-agent

Ports of [pstack](https://github.com/cursor/plugins/tree/main/pstack) — poteto's
Cursor plugin (MIT, by Lauren Tan / @poteto) — for two other agent harnesses.

- `opencode/` — the [opencode](https://opencode.ai) port.
- `prime-agent/` — the [prime-agent](https://github.com/PrimeIntellect-ai/prime-agent) port.
- `extras/` — upstream docs guide and the `benny` automation pack, copied verbatim.
  `benny` is a Cursor cloud-automation pack; it stays Cursor-shaped and only
  applies if you run its routines on Cursor (or re-home its prompts on your own
  automation host).
- `port.py` — the generator. Re-run it to re-derive both ports from a fresh
  upstream checkout: `python3 port.py --src <path/to/pstack>`.

See `ADAPTATIONS.md` for the Cursor → harness mapping table.
`UPSTREAM` records the exact upstream commit the outputs were generated from.

## Keeping in sync with upstream

```sh
uv run --with pyyaml python3 port.py --fetch        # download latest upstream, rebuild both ports
uv run --with pyyaml python3 port.py --check        # report drift without writing
```

## Keeping your changes

`GENERATED.sha256` records generated files. Before a build writes output,
`port.py` checks those hashes and stops if a generated file changed. Move the
change into `overrides/`, or rerun with `--force` after you review it.

Keep whole-file overrides under `overrides/<target>/`, mirroring the output
path. For smaller edits, save a unified diff as
`overrides/<target>/<path>.patch`. Create one from the repo root with:

```
diff -u --label a/skills/how/SKILL.md --label b/skills/how/SKILL.md   /tmp/SKILL.generated.md /tmp/SKILL.edited.md   > overrides/opencode/skills/how/SKILL.md.patch
```

`overrides/BASES.sha256` records the generated content replaced by whole-file
overrides. A warning means upstream changed that file and the override needs
review. `--strict` exits with an error for warnings.

Installers keep a `.pstack-install.sha256` manifest in the destination. They
update files that still match the last installed version, preserve local edits,
and write the new version beside an edited file as `<file>.pstack-new`.

.github/workflows/pstack-sync.yml runs `--fetch` weekly and opens a PR
whenever the generated output changes, so upstream updates arrive as
reviewable diffs.

## Install

Both harnesses at once (or pick one):

```sh
./install.sh             # install into prime-agent and opencode
./install.sh prime       # ~/.prime/agent only
./install.sh opencode    # ~/.config/opencode only

./uninstall.sh           # same targeting; removes only the files this repo
                         # installed and leaves local edits untouched
```

Destinations default to `~/.prime/agent` and `~/.config/opencode`; override
with `PRIME_DST` / `OPENCODE_DST`. Per-harness scripts are below — the
wrappers just call them.

### opencode

```sh
./opencode/install.sh            # defaults to ~/.config/opencode
```

Then inside opencode: `/poteto-mode` (or select the `poteto` primary agent).
`/setup-pstack` writes the per-role model table to
`~/.config/opencode/pstack-models.md` and registers it in `opencode.json`'s
`instructions` — the equivalent of Cursor's always-applied `pstack-models.mdc` rule.

### prime-agent

```sh
./prime-agent/install.sh         # defaults to ~/.prime/agent
# or: add this directory to the `packages` array in ~/.prime/agent/settings.json
```

Then inside prime-agent: `/skill:poteto-mode` (explicit) or describe the task in
poteto's style; `/poteto` is a prompt template that expands to the same thing.
`/skill:setup-pstack` writes the per-role model table to
`~/.prime/agent/pstack-models.md` and keeps a pointer block in
`~/.prime/agent/AGENTS.md` — delete the pointer block when you do not want
pstack model config to apply. Uninstall leaves setup-pstack files in place.

## What each port contains

| Piece | opencode | prime-agent |
|---|---|---|
| 50 skills (incl. `poteto-mode` + 21 principle leaf skills + playbooks/references) | `skills/*/SKILL.md` | `skills/*/SKILL.md` |
| `poteto-mode` activation | `/poteto-mode` command or the `poteto` primary agent | `/skill:poteto-mode` or `/poteto` prompt |
| Model config | `/setup-pstack` → `pstack-models.md` + `instructions` | `/skill:setup-pstack` → `pstack-models.md` + pointer block in `AGENTS.md` |
| Subagent specs | `agents/poteto-agent.md`, `comment-sicko.md` (`mode: subagent`) | `agents/*.md` (spec docs; `no-comments` embeds Comment Sicko as a spawn prompt) |
| Scripts | `poteto-mode/scripts/` (`watch-pr`, `orch`, `check-plan.mjs`, `worktree-audit.sh`) — verbatim | same |

Scripts are copied unmodified (they are forge/git helpers, not Cursor APIs);
`worktree-audit.sh` honors `PSTACK_TRANSCRIPTS_DIR` if your session store is not
Cursor-shaped.
