---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget.
  Detects your available models and writes an always-applied rule that overrides the
  skill defaults. Use for `/setup-pstack`, "configure pstack models", "pstack budget",
  or changing pstack's model choices.
license: MIT
compatibility: opencode
---

# Setup pstack (opencode)

Write `~/.config/opencode/pstack-models.md`, the model-role table pstack skills read, and register it in `~/.config/opencode/opencode.json`'s `instructions` array so it is always applied (the equivalent of Cursor's always-applied rule).

## Steps

### 1. Detect available models

Run `opencode models` and read the `provider/model` slugs the user has configured, plus any agents already pinned in `opencode.json`. If you cannot detect any, ask the user to paste the slugs they have access to. Never write a real slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.

### 2. Load current state

The default role-to-model mapping is the table shown in step 5 below. If `~/.config/opencode/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer a structured choice prompt over free text. Offer these four options with these exact labels, and name the current budget when the file records one.

- `unlimited — strongest slug per role`
- `large — strong slugs, fast tier where a role tolerates it`
- `medium — mid-tier slugs`
- `small — cheapest slugs`

**(b) Apply it.** Effort tokens don't exist in `provider/model` slugs, so budget moves between model tiers instead: keep each role's configured family but swap the member (for example `anthropic/claude-haiku-4-5` → `anthropic/claude-sonnet-4-5` → `anthropic/claude-opus-4-5` as budget rises). `inherit-parent` and `auto` do not change. If the result is not a detected slug, use the same family's detected slug nearest the target tier, else mark the role as needing a choice.

**(c) Show the roles and confirm.** Show every role with its model, marking any real slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent agent's model — opencode subagents inherit the invoking agent's model when `model` is omitted). For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it whose model family differs from the parent's when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen real slug is not available, stop and ask again.

### 5. Write the file

Write `~/.config/opencode/pstack-models.md` with a `# budget` line naming the chosen label and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Then ensure `~/.config/opencode/opencode.json` contains `"~/.config/opencode/pstack-models.md"`-equivalent entry in its `instructions` array (add the file's absolute path if missing; leave any existing entries intact). Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent model (omit the task `model`). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited
feature, refactoring: anthropic/claude-haiku-4-5
bug-fix: anthropic/claude-haiku-4-5
perf-issue: anthropic/claude-haiku-4-5
hillclimb: anthropic/claude-haiku-4-5
judgment and prose: anthropic/claude-opus-4-5
hardest tasks: anthropic/claude-opus-4-5
how explorer: anthropic/claude-haiku-4-5
how explainer: anthropic/claude-opus-4-5
why investigators: anthropic/claude-haiku-4-5
why synthesizer: anthropic/claude-opus-4-5
reflect tooling: openai/gpt-5.1
reflect judgment, divergent, synthesizer: anthropic/claude-opus-4-5
arena runners: anthropic/claude-opus-4-5, openai/gpt-5.1, anthropic/claude-haiku-4-5
arena cross-judge pool: anthropic/claude-opus-4-5, openai/gpt-5.1, anthropic/claude-haiku-4-5
swarm workers: anthropic/claude-haiku-4-5
architect runners: anthropic/claude-opus-4-5, openai/gpt-5.1, anthropic/claude-haiku-4-5
interrogate reviewers: anthropic/claude-opus-4-5, openai/gpt-5.1, anthropic/claude-haiku-4-5
```

### 6. Confirm

Tell the user the file was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with the `create-verification-skill` skill." On yes, load and run `create-verification-skill`. On no, move on without pushing.
