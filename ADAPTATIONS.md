# ADAPTATIONS: Cursor → opencode / prime-agent

pstack upstream leans on Cursor-specific mechanisms. This is the mapping each
port applies.

## Invocation surface

| Cursor | opencode | prime-agent |
|---|---|---|
| Skill frontmatter `name`/`description` + extras (`icon`, `color`, `reminder`, `disable-model-invocation`, `is_background`, `mode`) | `name`, `description`, `license`, `compatibility`; extras moved to `metadata:`; every skill also sets `metadata.opencode/autoinvoke: false` so opencode keeps it out of the available-skills list (still loadable by ID) | `name`, `description`; `disable-model-invocation` preserved verbatim (hides from `<available_skills>`, still `/skill:name`-invocable) |
| `/skillname` slash mention in prose | `the \`name\` skill` (except `/poteto-mode`, `/setup-pstack` — shipped as real commands) | `` `/skill:name` `` |
| `mode: true` on poteto-mode (agent mode) | `agents/poteto.md` with `mode: primary` | not applicable — load the skill; `/poteto` prompt provided |
| `~/.cursor/rules/pstack-models.mdc` always-applied rule | `~/.config/opencode/pstack-models.md` + `instructions` entry in `opencode.json` | `~/.prime/agent/pstack-models.md` + pointer block in `AGENTS.md` (delete the block to disable) |
| Cursor model slugs (`grok-4.7-xhigh-fast`, `claude-opus-5-5-max`, `gpt-5.6-sol-max`; effort suffixes `-max`/`-xhigh`/`-high`/`-medium`/`-low`, `-fast`) | slug-free: every role defaults to `inherit-parent` (omit `model`, run on the parent model). Define per-role `provider/model` slugs once via `/setup-pstack`, which detects `opencode models`. No slug is hard-coded into skill bodies. | same slug-free default; effort ladder maps to `thinking` levels `off`/`low`/`medium`/`high` on `rlm.spawn` when a role sets a real model |

## Subagents and background work

| Cursor | opencode | prime-agent |
|---|---|---|
| `Task` tool, `subagent_type: generalPurpose` | `task` tool, the `general` subagent | `rlm.spawn(prompt, name=, model=, thinking=)` + `rlm.collect` |
| `subagent_type: "poteto-agent"` | `agents/poteto-agent.md` (`mode: subagent`) | inline spec paragraph prepended to the spawn prompt (kept at `agents/poteto-agent.md`) |
| `subagent_type: "Comment Sicko"` | `agents/comment-sicko.md` (`mode: subagent`) | `no-comments/references/comment-sicko.md` inlined into the spawn prompt |
| `run_in_background`, `environment: "cloud"` | background/detached `task` subagents | children are concurrent by default; long-lived work uses resident sessions via `rlm.create_session` |
| `readonly: true` (strips MCPs) | deny edit/write tools or a read-only brief in the prompt | read-only brief in the prompt (no permission flag on `rlm.spawn`) |
| nesting depth 3 | subagent fan-out (depth as supported) | `rlm.spawn` children; `rlm.factory` for joins/gates/loops |

## Periodic wakes and review bots

| Cursor | opencode | prime-agent |
|---|---|---|
| `/loop` recurring command | background task / watcher that re-arms (`scripts/watch-pr` covers GitHub PRs) | `rlm-heartbeat`, a goal, or a daemon schedule |
| `Bugbot` PR review bot | forge-generic: "whatever bot posts review comments on your PRs" (kept as a glossary term) | same |
| Cursor cloud agent / `environment: "cloud"` | dedicated background subagent session | resident depth-0 session (`rlm.create_session`) |
| transcripts at `~/.cursor/projects/<slug>/agent-transcripts/` | `poteto-mode/scripts/transcripts.py` exports this workspace's sessions via `opencode session list/export` (sessions live in `opencode.db`) | same script, reading `~/.prime/agent/sessions/*.jsonl` filtered by `cwd` |

## Imported team-kit skills

`deslop`, `control-cli`, `control-ui`, and the other harness-neutral team-kit
skills are included in both ports. Each target includes
`LICENSE-cursor-team-kit`. `pr-review-canvas` is skipped because it needs
Cursor's embedded browser. The remaining team-kit skills use local files, git,
`gh`, or harness sessions.
- `create-skill` and `/loop` remain Cursor built-ins. Opencode uses the
  `authoring-a-skill` playbook; prime-agent uses `skill-creator`.
- `Origin` forge CLI references are left verbatim — upstream already gates them
  on `command -v origin`, with `gh` fallback.
- `benny` automation pack + docs guide are in `extras/` verbatim (Cursor-shaped
  by design).
