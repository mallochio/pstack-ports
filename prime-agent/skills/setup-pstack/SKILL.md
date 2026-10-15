---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget.
  Detects your available models and writes an always-applied rule that overrides the
  skill defaults. Use for `/skill:setup-pstack`, "configure pstack models", "pstack
  budget", or changing pstack's model choices.
---

# Setup pstack (prime-agent)

Write `~/.prime/agent/pstack-models.md`, the model-role table pstack skills read, and keep a pointer block in `~/.prime/agent/AGENTS.md` so the file stays discoverable. The pointer block is delimited by `<!-- pstack:start -->` and `<!-- pstack:end -->`; AGENTS.md content outside it is never touched. To stop applying pstack models, delete the pointer block (and the file if you no longer want it).

## Steps

### 1. Detect available models

From the kernel, call `await rlm.find_models()` (optionally with a query per family) to list the `provider/model` selectors backed by active credentials. If you cannot detect any, ask the user for the slugs they can use. Never write a real slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.

### 2. Load current state

Every role defaults to `inherit-parent` (omit `model`, run on the parent model), so the file works with zero hard-coded slugs. If `~/.prime/agent/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. If only a legacy `pstack-models` block (`<!-- pstack-models:start -->` … `<!-- pstack-models:end -->`) exists inside `~/.prime/agent/AGENTS.md`, migrate its values into the new file. Otherwise start from all-`inherit-parent`. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer a structured choice prompt over free text. Offer these four options with these exact labels, and name the current budget when the file records one.

- `unlimited — thinking=high`
- `large — thinking=high, medium where a role tolerates it`
- `medium — thinking=medium`
- `small — thinking=low`

**(b) Apply it.** Budget maps to the `thinking` level `rlm.spawn` passes per role, and to model tier inside each family when the configured family has a cheaper member (e.g. haiku/sonnet/opus). Record the chosen thinking level on the `# budget` line; roles keep their configured model unless the budget calls for a tier change. `inherit-parent` and `auto` do not change (they omit `model`, so the child inherits the parent model, and `thinking` defaults to the parent level). If the result is not a detected slug, use the same family's detected slug nearest the target tier, else mark the role as needing a choice.

**(c) Show the roles and confirm.** Show every role with its model and thinking level, marking any real slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one child spawns per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it whose model family differs from the parent's when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen real slug is not available, stop and ask again.

### 5. Write the file and pointer

Write `~/.prime/agent/pstack-models.md` with a `# budget` line naming the chosen label and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default (inherit-parent).
# `inherit-parent` or `auto` as a value: omit `model` on rlm.spawn so the child runs on the parent model. Alias entries in a panel list still count toward its fan-out.
# A value may append `; thinking=<level>` to set the spawn's reasoning level (`off|low|medium|high`).
# budget: unlimited (thinking=high)
feature, refactoring: inherit-parent
bug-fix: inherit-parent
perf-issue: inherit-parent
hillclimb: inherit-parent
judgment and prose: inherit-parent
hardest tasks: inherit-parent
how explorer: inherit-parent
how explainer: inherit-parent
why investigators: inherit-parent
why synthesizer: inherit-parent
reflect tooling: inherit-parent
reflect judgment, divergent, synthesizer: inherit-parent
arena runners: inherit-parent
arena cross-judge pool: inherit-parent
swarm workers: inherit-parent
architect runners: inherit-parent
interrogate reviewers: inherit-parent
```

Then ensure `~/.prime/agent/AGENTS.md` contains the pointer block (create AGENTS.md if absent). If a legacy `<!-- pstack-models:start -->` block is present, delete it and keep a single `<!-- pstack:start -->` block. Keep content outside the markers byte-identical. Pointer shape:

```
<!-- pstack:start -->
pstack per-role models live in `pstack-models.md` in this directory (written by `/skill:setup-pstack`). Remove this block (and the file) to stop applying them.
<!-- pstack:end -->
```

### 6. Confirm

Tell the user the file was written and that it applies to new sessions. Re-running this skill updates it. Mention that deleting the pointer block disables pstack model config without touching the rest of AGENTS.md.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with `/skill:create-verification-skill`." On yes, invoke `/skill:create-verification-skill`. On no, move on without pushing.
