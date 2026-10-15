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
python3 port.py --fetch        # download latest upstream, rebuild both ports
python3 port.py --check        # report drift without writing (dry run)
```

The generated trees are rebuilt wholesale, so keep hand-edits out of
`opencode/` and `prime-agent/`. To patch generated output durably, drop a
file under `overrides/<target>/` mirroring the output path — it is copied
verbatim over the result at the end of every build:

```
overrides/opencode/skills/how/SKILL.md      # replaces the generated file
overrides/prime-agent/prompts/poteto.md     # same for prime-agent
```

`.github/workflows/pstack-sync.yml` runs `--fetch` weekly and opens a PR
whenever the generated output changes, so upstream updates arrive as
reviewable diffs.

## Install

### opencode

```sh
./opencode/install.sh            # defaults to ~/.config/opencode
# or: cp -R opencode/skills opencode/agents opencode/commands ~/.config/opencode/
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
`/skill:setup-pstack` writes the `pstack-models` block into
`~/.prime/agent/AGENTS.md`.

## What each port contains

| Piece | opencode | prime-agent |
|---|---|---|
| 50 skills (incl. `poteto-mode` + 21 principle leaf skills + playbooks/references) | `skills/*/SKILL.md` | `skills/*/SKILL.md` |
| `poteto-mode` activation | `/poteto-mode` command or the `poteto` primary agent | `/skill:poteto-mode` or `/poteto` prompt |
| Model config | `/setup-pstack` → `pstack-models.md` + `instructions` | `/skill:setup-pstack` → `AGENTS.md` block |
| Subagent specs | `agents/poteto-agent.md`, `comment-sicko.md` (`mode: subagent`) | `agents/*.md` (spec docs; `no-comments` embeds Comment Sicko as a spawn prompt) |
| Scripts | `poteto-mode/scripts/` (`watch-pr`, `orch`, `check-plan.mjs`, `worktree-audit.sh`) — verbatim | same |

Scripts are copied unmodified (they are forge/git helpers, not Cursor APIs);
`worktree-audit.sh` honors `PSTACK_TRANSCRIPTS_DIR` if your session store is not
Cursor-shaped.
