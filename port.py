#!/usr/bin/env python3
"""Port pstack (Cursor plugin by poteto/Lauren Tan) to other agent harnesses.

Reads the upstream tree at ../cursor-plugins/pstack (or --src) and emits:

  opencode/      skills + agents + commands installable into ~/.config/opencode/
  prime-agent/   a prime-agent package (skills + prompts + agent specs),
                 installable via ~/.prime/agent/ copies or a `packages` entry

Usage: python3 port.py [--src DIR] [--dst DIR]
Re-runnable; output dirs are regenerated wholesale.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Per-harness constants
# ---------------------------------------------------------------------------

# Default role models. Cursor slugs embed an effort ladder (…-max/-xhigh/…-fast);
# the targets use plain provider/model selectors, so defaults are chosen to keep
# the fast-vs-judgment split and are overridable via the setup-pstack skill.
MODELS = {
    "fast_code": "anthropic/claude-haiku-4-5",
    "judgment": "anthropic/claude-opus-4-5",
    "panel_third": "openai/gpt-5.1",
}

CURSOR_SLUGS = {
    "grok-4.7-xhigh-fast": MODELS["fast_code"],
    "grok-4.7-medium-fast": MODELS["fast_code"],
    "claude-opus-5-5-max": MODELS["judgment"],
    "claude-opus-5-5-medium": MODELS["judgment"],
    "gpt-5.6-sol-max": MODELS["panel_third"],
}

MODELS_FILE = {
    "opencode": "`~/.config/opencode/pstack-models.md`",
    "prime-agent": "the `pstack-models` block in `~/.prime/agent/AGENTS.md`",
}
MODELS_BASENAME = {
    "opencode": "pstack-models.md",
    "prime-agent": "`pstack-models` block",
}

# Skills that exist inside pstack (so `/name` means "the name skill").
PSTACK_SKILLS = {
    "architect", "arena", "automate-me", "benchmark-checklist", "blast-radius",
    "bro", "correct", "create-verification-skill", "figure-it-out", "how",
    "interrogate", "maintain-verification-skill", "make-bot-ui", "no-comments",
    "poteto-mode", "recall", "reflect", "setup-pstack", "show-me-your-work",
    "swarm", "tdd", "teach", "technical-writing", "typescript-best-practices",
    "unslop", "why",
}

# Skills owned by other Cursor plugins / built-ins we reference conditionally.
EXTERNAL_SKILLS = {"deslop", "control-cli", "control-ui", "create-skill", "babysit"}

ALL_SLASH = PSTACK_SKILLS | EXTERNAL_SKILLS | {"no-comments"}


def slash_repl(target: str, name: str) -> str:
    if target == "prime-agent":
        return f"`/skill:{name}`"
    # opencode: /poteto-mode and /setup-pstack are real shipped commands.
    if name in {"poteto-mode", "setup-pstack"}:
        return f"`/{name}`"
    return f"the `{name}` skill"


# ---------------------------------------------------------------------------
# Generic body rewrites. (pattern, replacement per target) applied in order.
# Patterns are regexes.
# ---------------------------------------------------------------------------

def common_rewrites(target: str) -> list[tuple[str, str]]:
    is_prime = target == "prime-agent"
    task_call = r"`rlm\.spawn` call" if is_prime else "task call"
    task_tool = "the `rlm.spawn` API" if is_prime else "the Task tool"
    task_sub = "an `rlm.spawn` child" if is_prime else "a Task subagent"

    rules: list[tuple[str, str]] = [
        # ---- model config file -------------------------------------------------
        (re.escape("~/.cursor/rules/pstack-models.mdc"),
         "~/.config/opencode/pstack-models.md" if not is_prime
         else "the `pstack-models` block in `~/.prime/agent/AGENTS.md`"),
        (r"`pstack-models\.mdc`",
         "`pstack-models.md`" if not is_prime else "`pstack-models` block"),
        (r"pstack-models\.mdc",
         "pstack-models.md" if not is_prime else "pstack-models block"),
        # ---- transcripts -------------------------------------------------------
        (r"`~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>\.jsonl`[^.]*"
         r"(where `<slug>` is the workspace path with the leading slash dropped and each \"/\" turned into \"-\" \(so `/Users/you/proj` becomes `Users-you-proj`\))?\.\s*Every line is one chat message\.",
         ("the session transcript for this workspace under prime-agent's session "
          "store (`~/.prime/agent/sessions/*.jsonl`). Every line is one session entry.")
         if is_prime else
         ("the session transcript for this workspace under the harness's session "
          "store (opencode keeps sessions under `~/.local/share/opencode/storage/`; "
          "locate this session's file rather than globbing every workspace).")),
        (r"the active workspace's `agent-transcripts/` directory \(the system prompt names the path\. Do not glob across `~/.cursor/projects/\*/`, that crosses workspace boundaries and reads private chats from unrelated projects\)",
         "this session's transcript directory (do not glob across "
         "`~/.prime/agent/sessions/` entries outside this session — that crosses "
         "workspace boundaries and reads private chats from unrelated projects)"
         if is_prime else
         "this session's transcript directory (do not glob across the harness's "
         "other workspaces' session stores — that crosses workspace boundaries "
         "and reads private chats from unrelated projects)"),
        (r"a cloud-agent URL",
         "a resident-session URL" if is_prime else "a background-session URL"),
        (r"the active workspace's `agent-transcripts/` directory",
         "this session's transcript directory" if not is_prime else
         "`~/.prime/agent/sessions/`"),
        (r"local transcripts under `agent-transcripts/`",
         "local session transcripts under `~/.prime/agent/sessions/`" if is_prime else
         "local transcripts under the harness's session store"),
        (r"`agent-transcripts/`",
         "`~/.prime/agent/sessions/`" if is_prime else "the harness's session store"),
        (r"~/.cursor/projects/\*/",
         "the harness's other workspaces' session stores" if not is_prime else
         "~/.prime/agent/sessions/ entries outside this session"),
        (r"the Cursor dashboard", "the harness's session list" if not is_prime else
         "the daemon's session list"),
        (r"After a Cursor restart: local agents are dead, cloud work is not\.",
         "After a harness restart: spawned children are dead, resident sessions are not."
         if is_prime else
         "After a harness restart: running subagents are dead, detached sessions are not."),
        (r"Re-read the standing orders", "Re-read the standing orders"),
        (r"reattach cloud work", "reattach detached work"),
        (r"a Cursor restart", "a harness restart"),
        (r"`git show origin/main:pstack/(skills/[^`]+)`",
         lambda m: "read the installed copy at `{}`".format(
             ("~/.prime/agent/" if is_prime else "~/.config/opencode/") + m.group(1))),
        (r"`git show origin/main:<control skill path>`",
         "read the installed control skill (if one is installed)"),
        # ---- make-bot-ui: automation-host agnostic ----------------------------
        (r"Call `update_state` with target `routine` and action `create`\. Set these fields:",
         "Create a webhook routine with the automation host this harness runs (Cursor routines, "
         "a webhook responder, or a scheduled-agent inbox — whichever you configured for benny). "
         "Set these fields:"),
        (r"If `update_state` shows a confirm card, wait for the user to confirm\.",
         "If the automation host asks for confirmation, wait for the user to confirm."),
        (r"1\. Click this agent's name in the chat header, or press \*\*Cmd\+Shift\+I\*\*\.\n"
         r"2\. Find the \*\*Routines\*\* list under the computer preview\.\n"
         r"3\. Open this webhook routine\.\n"
         r"4\. Copy the webhook URL\. The user may paste the URL in chat\.\n"
         r"5\. Copy the sender key\. The user must not paste the sender key in chat\.",
         "1. Open the routine in the automation host's panel/UI.\n"
         "2. Copy the webhook URL. The user may paste the URL in chat.\n"
         "3. Copy the sender key. The user must not paste the sender key in chat."),
        (r"The URL looks like `https://api2\.cursor\.sh/automations/webhook/<id>` with no query string\.",
         "On Cursor routines the URL looks like "
         "`https://api2.cursor.sh/automations/webhook/<id>` with no query string; "
         "other automation hosts expose an equivalent URL."),
        # ---- skills dirs --------------------------------------------------------
        (r"\.cursor/skills/",
         ".opencode/skills/" if not is_prime else ".prime/agent/skills/"),
        (r"~/.cursor/skills/",
         "~/.config/opencode/skills/" if not is_prime else "~/.prime/agent/skills/"),
        (r"~/.cursor/plugins/",
         "~/.config/opencode/" if not is_prime else "installed package paths under `~/.prime/agent/`"),
        (r"the Cursor environment\. Use the available-tools map when present\. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers\.",
         ("the harness environment. Use the available-tools map when present, or "
          "the configured MCP server list in opencode.json.") if not is_prime else
         ("the harness environment through the kernel (`rlm.mcp` / `mcp_status`).")),
        # ---- cursor-team-kit / built-in externals --------------------------------
        (r"a slop-strip \(the `deslop` skill from the `cursor-team-kit` plugin \(`/deslop`\)\)",
         "a slop-strip pass (the `deslop` skill if your setup provides it, else a "
         "hand pass against the **unslop** checklist)"),
        (r"the `deslop` skill from the `cursor-team-kit` plugin \(`/deslop`\)",
         "a `deslop` slop-strip if your setup provides it, else a hand pass "
         "against the **unslop** checklist"),
        (r"`cursor-team-kit` publishes `control-cli` \(CLIs and TUIs\) and `control-ui` \(browser / Electron / web UIs\)\.?",
         "Use `control-cli` for CLIs/TUIs or `control-ui` for browser/web UIs when your "
         "setup ships them; otherwise drive the real surface directly."),
        (r"the matching control skill \(such as `control-ui` or `control-cli` from `cursor-team-kit`\)",
         "the matching control-surface skill (`control-ui`/`control-cli` upstream — "
         "if installed, else drive the real surface directly)"),
        (r"\(such as `control-ui` or `control-cli`[^)]*\)",
         "(a control-surface skill such as `control-ui`/`control-cli` if installed, "
         "else the real surface directly)"),
        (r"(`control-ui` or `control-cli`) from `cursor-team-kit`",
         r"\1 (upstream Cursor plugin — if installed)"),
        (r"(`control-(ui|cli)`) from `cursor-team-kit`",
         r"\1 (upstream Cursor plugin — if installed)"),
        (r"(`?/?control-(ui|cli)`?|/control-(ui|cli)) from `cursor-team-kit`",
         "a control-surface skill (upstream Cursor plugin — if installed)"),
        (r"\(from `cursor-team-kit`\)",
         "(upstream Cursor plugin — use it if installed, else do the pass by hand)"),
        (r" from `cursor-team-kit`| from the `cursor-team-kit` plugin",
         " (upstream Cursor plugin — use it if installed, else do the pass by hand)"),
        (r" of `cursor-team-kit`", " of the upstream Cursor plugin"),
        (r"`cursor-team-kit`", "the upstream Cursor plugin"),
        (r"the matching control skill",
         "the matching control-surface skill when one is installed, else the real surface directly"),
        (r"the \*\*create-skill\*\* skill \(Cursor's built-in for authoring SKILL\.md files\)",
         "the `skill-creator` skill" if is_prime else
         "the `authoring-a-skill` playbook's conventions"),
        (r"Cursor's built-in `create-skill` \(authoring\)",
         "prime-agent's built-in `skill-creator` (authoring)" if is_prime else
         "the `authoring-a-skill` playbook (authoring)"),
        (r"Cursor's built-in `create-skill` skill",
         "the `skill-creator` skill" if is_prime else
         "the `authoring-a-skill` playbook"),
        (r", and not Cursor's built-in babysit skill, whose description matches the same words",
         ", and not any built-in babysit helper"),
        (r"Cursor's built-in babysit skill",
         "a built-in babysit helper"),
        # ---- subagent mechanics --------------------------------------------------
        (r"Substituting `generalPurpose` skips",
         "Substituting a plain child skips"),
        (r"`?subagent_type`?: ?`?\"?generalPurpose\"?`?",
         "a plain `rlm.spawn` child" if is_prime else "the `general` subagent"),
        (r"`subagent_type: ?\"poteto-agent\"`|subagent_type: \"poteto-agent\"",
         "an `rlm.spawn` child whose prompt opens with the `poteto-agent` spec paragraph"
         if is_prime else "the `poteto-agent` subagent"),
        (r"`subagent_type: \"Comment Sicko\"`|subagent_type: \"Comment Sicko\"",
         "an `rlm.spawn` child whose prompt is `references/comment-sicko.md`"
         if is_prime else "the `comment-sicko` subagent"),
        (r"`subagent_type`:",
         "child spec:" if is_prime else "`subagent_type`:"),
        (r"Spawn one Task subagent", "Spawn one %s" % ("`rlm.spawn` child" if is_prime else "Task subagent")),
        (r"one Task subagent", "one %s" % ("`rlm.spawn` child" if is_prime else "Task subagent")),
        (r"Task subagent", "RLM child" if is_prime else "Task subagent"),
        (r"full Task schema including `environment`",
         "full `rlm.spawn` signature" if is_prime else
         "full `task` schema"),
        (r"the Task tool", "the `rlm.spawn` API" if is_prime else "the `task` tool"),
        (r"(?<!the )Task tool", "`rlm.spawn` API" if is_prime else "`task` tool"),
        (r"one `Task` call", "one %s" % ("`rlm.spawn` call" if is_prime else "`task` call")),
        (r"three `Task` calls", "three %s" % ("`rlm.spawn` calls" if is_prime else "`task` calls")),
        (r"single message using the Task tool",
         "single fan-out of `rlm.spawn` calls" if is_prime else "single message using the Task tool"),
        (r"`Task` calls", "`rlm.spawn` calls" if is_prime else "`task` calls"),
        (r"`Task` call", "`rlm.spawn` call" if is_prime else "`task` call"),
        (r"\bTask calls\b", "`rlm.spawn` calls" if is_prime else "task calls"),
        (r"`Task`", "`rlm.spawn`" if is_prime else "`task`"),
        (r"\bTask\b(?= with)", "`rlm.spawn`" if is_prime else "`task`"),
        (r"Spawn `Task`", "Spawn %s" % ("an `rlm.spawn` child" if is_prime else "a `task`")),
        # run_in_background
        (r"`run_in_background: true`",
         "children run concurrently once spawned — collect them with `rlm.collect`"
         if is_prime else "run in the background where the task tool supports it"),
        (r"run_in_background: true", "concurrent children" if is_prime else "background tasks"),
        # agent mode / readonly
        (r"agent mode \(`readonly: false`\)|agent mode \(readonly strips MCP\)",
         "full tool access"),
        (r"- `readonly`: `true`",
         "- read-only brief: instruct the child it must not modify files or run "
         "mutating commands"),
        (r"- `readonly`: `false` \(agent mode\)\.",
         "- tool access: full."),
        (r"readonly/Ask mode", "a read-only tool restriction"),
        (r"Readonly/Ask mode", "A read-only tool restriction"),
        (r"readonly strips MCPs?",
         "read-only modes strip MCPs" if is_prime else "denied tools strip MCPs"),
        (r"Readonly strips MCPs",
         "Read-only modes strip MCPs" if is_prime else "Denied tools strip MCPs"),
        (r"one readonly judge subagent",
         "one read-only judge child" if is_prime else "one read-only judge subagent"),
        (r"Always `environment: \"cloud\"` unless the task needs this machine",
         "Always a spawned child (a daemon session via `rlm.create_session` when the work must outlive this session) unless the task needs this machine"
         if is_prime else
         "Always a detached background subagent session unless the task needs this machine"),
        (r"`environment: \"cloud\"`, ", ""),
        (r"`environment: \"cloud\"`",
         "a resident/daemon child" if is_prime else "a detached session"),
        (r"`environment: \"local\"`",
         "a child on this machine" if is_prime else "a local task"),
        (r"Cursor cloud agent",
         "resident depth-0 session (`rlm.create_session`)" if is_prime else
         "dedicated background subagent session"),
        (r"cloud agents", "resident sessions" if is_prime else "background sessions"),
        (r"cloud agent", "resident session" if is_prime else "background session"),
        (r"Cloud agents", "Resident sessions" if is_prime else "Background sessions"),
        (r"Cloud agent", "Resident session" if is_prime else "Background session"),
        (r"in local and cloud roots", "in interactive and daemon sessions" if is_prime else
         "in local and background sessions"),
        # ---- AskQuestion ----------------------------------------------------------
        (r"Prefer `?AskQuestion`? over free text\.",
         "Prefer a structured choice prompt over free text where the UI offers one."),
        (r"confirm intent with `AskQuestion`",
         "confirm intent with a structured question"),
        (r"Use the `AskQuestion` tool \(structured multi-choice\) rather than asking the user to type from scratch\.",
         "Use a structured multi-choice question where the harness UI offers one rather "
         "than asking the user to type from scratch."),
        (r"About to `AskQuestion`",
         "About to ask the human a structured question"),
        (r"`AskQuestion`", "a structured question"),
        (r"AskQuestion", "a structured question"),
        # ---- /loop -----------------------------------------------------------------
        (r"Cursor's `/loop` command \(a built-in, not a pstack skill\)",
         "a heartbeat/scheduled wake (goals, schedules, `rlm-heartbeat`)" if is_prime else
         "a re-armed wake (a background watcher task or repeated checks)"),
        (r"Cursor's `/loop` command",
         "a heartbeat/scheduled wake" if is_prime else "a re-armed wake"),
        (r"Run `drive` and `background` under `/loop` in dynamic mode\.",
         "Keep `drive` and `background` alive on a heartbeat or scheduled wake."
         if is_prime else
         "Keep `drive` and `background` alive on a re-armed wake."),
        (r"Hold the watch under `/loop` in dynamic mode\.",
         "Hold the watch on a scheduled wake." if is_prime else
         "Hold the watch on a re-armed wake."),
        (r"arm `/loop 1h` with a prompt that runs this tick",
         "arm a 1-hour heartbeat/scheduled wake whose prompt runs this tick" if is_prime else
         "arm a 1-hour re-wake (background task) whose prompt runs this tick"),
        (r"`/loop` works in local and cloud roots\.",
         "Scheduled wakes work in interactive and daemon sessions." if is_prime else
         "Re-wakes work in interactive and background sessions."),
        (r"`/loop` per component until the diff is zero\.",
         "Re-check each component on a periodic wake until the diff is zero."),
        (r"/loop 1h", "a 1h periodic wake" if not is_prime else "a 1h scheduled wake"),
        (r"`/loop`", "a periodic wake" if not is_prime else "a scheduled wake"),
        (r"/loop\b", "a periodic wake" if not is_prime else "a scheduled wake"),
        # ---- model slugs ------------------------------------------------------------
        *[(re.escape(k), v) for k, v in CURSOR_SLUGS.items()],
        # ---- forge/watch ------------------------------------------------------------
        (r"re-read this playbook from trunk with `git show origin/main:pstack/",
         "re-read this playbook from your installed pstack copy at `"),
    ]
    return rules


# Slash-mention rewrite, applied after common rules so special sentences win.
def rewrite_slashes(text: str, target: str) -> str:
    names = "|".join(sorted(re.escape(n) for n in ALL_SLASH | PSTACK_SKILLS))
    # Optional surrounding backticks are consumed so replacements never nest.
    # Lookbehind blocks path segments (playbooks/babysit.md); optional
    # surrounding backticks are consumed so replacements never nest.
    pat = re.compile(r"(?<![\w.:/-])`?/(" + names + r")\b`?(?![\w-])")

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        return slash_repl(target, name)

    return pat.sub(_sub, text)


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    import yaml  # deferred; pyyaml is on the box
    return yaml.safe_load(m.group(1)), text[m.end():]


def dump_frontmatter(fm: dict) -> str:
    import yaml
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip() + "\n---\n"


def skill_frontmatter(fm: dict, dirname: str, target: str) -> dict:
    out: dict = {"name": dirname}
    if "description" in fm:
        d = str(fm["description"]).strip().strip('"').strip("'")
        out["description"] = rewrite_slashes(d, target)
    else:
        out["description"] = dirname
    if target == "prime-agent":
        if fm.get("disable-model-invocation"):
            out["disable-model-invocation"] = True
    else:
        meta = {}
        for k in ("mode", "icon", "color", "reminder", "disable-model-invocation"):
            if k in fm:
                meta[k.replace("-", "_")] = str(fm[k]).lower() if isinstance(fm[k], bool) else str(fm[k])
        if meta:
            out["metadata"] = meta
        out["license"] = "MIT"
        out["compatibility"] = "opencode"
    return out


# ---------------------------------------------------------------------------
# poteto-mode SKILL.md targeted rewrites
# ---------------------------------------------------------------------------

POTETO_SUBAGENTS_OC = """**Spawn the `poteto-agent` subagent for any delegate you launch inside a playbook step** (code-writing delegates, ad-hoc helpers): it is installed as a `mode: subagent` agent, so a `task` call with subagent type `poteto-agent` gets the full style. `/poteto-mode` and `poteto-agent` route through the same body of rules. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own subagent/model mix for diverse-model review; respect what the skill prescribes, don't override to `poteto-agent`.

**Defaults for every `task` call.** Prefer parallel spawns: launch independent delegates in one message and keep the main thread on summaries. Pass file pointers, not inlined context. Set an explicit `model` per role (configurable via `/setup-pstack` — defaults `%s` for code, `%s` for prose and judgment). Code delegates tier by difficulty: the hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`%s`); trivial mechanical edits go to the fast code model. Per-role lines in the `pstack-models.md` file override these defaults and the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). A role with no line keeps its default, and a role line of `inherit-parent` or `auto` runs that role on the parent model (omit `model`). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`.

You own every subagent's work. Review the diff and write your own summary, don't pass through what it said. A second opinion is the same prompt against a different model. Agreement is high-signal.

**Fresh subagents by default.** Give new work to a fresh subagent with consolidated scope, meaning the original brief, every later directive, and the prior agent's report and branch. This holds for a fix round, a follow-up, a retry, and the next queue item. Resume or message an existing subagent only when the new work strictly needs state that lives in that agent and is costly to move: its local checkout, its uncommitted changes, or a process it still runs, such as a dev server or a babysit watcher. A stop or hold order to a running agent is not reuse. A role such as a PR owner outlives its agent. Once that agent returns, a fresh agent takes the role's next round. Interrupt-chained resumes silently drop directives, so fire a fresh subagent with consolidated scope rather than trusting a "done" summary.""" % (
    MODELS["fast_code"], MODELS["judgment"], MODELS["judgment"])

POTETO_SUBAGENTS_PRIME = """**Spawn every delegate inside a playbook step as an `rlm.spawn` child whose prompt opens with the poteto-agent spec**: `You are operating as poteto-mode's full agent style. Read the poteto-mode skill's SKILL.md in full before doing any work, including its inline Principles index. Navigate to a leaf principle-* skill whenever you apply that principle.` The same paragraph is kept at `agents/poteto-agent.md` in this package. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own model mix for diverse-model review; respect what the skill prescribes.

**Defaults for every `rlm.spawn` call.** Children run concurrently once spawned — fan out independent delegates, keep the main thread on summaries, and `rlm.collect` the results. Pass file pointers, not inlined context. Set an explicit `model` (`provider/model`) and `thinking` level per role (configurable via `/skill:setup-pstack` — defaults `%s` for code, `%s` for prose and judgment). Code delegates tier by difficulty: the hardest changes (cross-cutting design, gnarly concurrency, subtle algorithms) go to your strongest judgment model (`%s`, higher `thinking`); trivial mechanical edits go to the fast code model. Per-role lines in the `pstack-models` block of `~/.prime/agent/AGENTS.md` override these defaults and the model choices in the routed skills. A role with no line keeps its default, and a role line of `inherit-parent` or `auto` omits `model` so the child runs on the parent model. Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`. For a standing fan-out with joins, gates, or per-item children, prefer `rlm.factory` machines over ad-hoc spawn loops; enable it once with `/factory on`.

You own every subagent's work. Review the diff and write your own summary, don't pass through what it said. A second opinion is the same prompt against a different model. Agreement is high-signal.

**Fresh subagents by default.** Give new work to a fresh `rlm.spawn` child with consolidated scope, meaning the original brief, every later directive, and the prior agent's report and branch. This holds for a fix round, a follow-up, a retry, and the next queue item. Message an existing child (`agent_message.send`) only when the new work strictly needs state that lives in that child and is costly to move: its checkout, uncommitted changes, or a process it still runs, such as a dev server or a babysit watcher. A stop or hold order to a running child is not reuse. A role such as a PR owner outlives its child. Once that child returns, a fresh child takes the role's next round. Resumed children silently drop directives, so fire a fresh child with consolidated scope rather than trusting a "done" summary.""" % (
    MODELS["fast_code"], MODELS["judgment"], MODELS["judgment"])

# Additional poteto-mode body tweaks per target (list of (old, {oc, prime})).
POTETO_EDITS = [
    ("Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
     {"opencode": "Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the `authoring-a-skill` playbook's conventions for SKILL.md files.",
      "prime-agent": "Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the `skill-creator` skill (prime-agent's built-in for authoring SKILL.md files)."}),
    ("- Bug fix. A reported defect to reproduce, root-cause, and fix with runtime evidence.",
     None),  # unchanged, anchor only
]

HARNESS_NOTE = {
    "opencode": (
        "## Harness notes (opencode port)\n\n"
        "- Skills load on demand through the `skill` tool; `/poteto-mode` and `/setup-pstack` are real commands installed by this port. Other `/name` mentions in the ported text mean \"load the `name` skill\".\n"
        "- Subagents run through the `task` tool; `poteto-agent` and `comment-sicko` are installed as `mode: subagent` agents, and `poteto` is installed as a `mode: primary` agent you can Tab to.\n"
        "- The model role table lives at `~/.config/opencode/pstack-models.md` (registered in `opencode.json` `instructions`); `/setup-pstack` writes it.\n"
        "- `Bugbot` means the forge's automated PR reviewer — whatever bot posts review comments on your PRs (Bugbot on Cursor; Copilot review, Devin Review, or a CI bot elsewhere). Triage it skeptically per `references/bugbot-triage.md`.\n"
        "- `Origin` is optional: where a playbook says `origin pr ...`, it applies only when the `origin` CLI exists and resolves the repo; otherwise stay on `gh`.\n"
        "- Where text references a periodic wake (`/loop` upstream), use a background task or watcher that re-arms; `scripts/watch-pr` covers GitHub PR wakes.\n"
        "- The `deslop`/`control-cli`/`control-ui` skills live in a different upstream plugin. Where they're named, use them if your setup ships them, else do the equivalent pass by hand.\n"
        "- The verification-skill convention (`.opencode/skills/verify-<app>/`) works as-is: project-local skills are discovered automatically.\n"
    ),
    "prime-agent": (
        "## Harness notes (prime-agent port)\n\n"
        "- Skills load via the skill inventory; explicit invocation is `/skill:<name>` (for example `/skill:poteto-mode`). `disable-model-invocation` skills stay hidden until invoked.\n"
        "- Subagents are spawned children: `await rlm.spawn(prompt, name=..., model='provider/model', thinking='low|medium|high')`, collected with `rlm.collect`, and inspected with `agent_observe`/`agent_message`. For shaped fan-out (joins, bounded loops, per-item children) enable and use `rlm.factory` (`/factory on`).\n"
        "- The model role table lives in a managed `pstack-models` block inside `~/.prime/agent/AGENTS.md`; `/skill:setup-pstack` writes it and `/skill:recall`-style transcripts live under `~/.prime/agent/sessions/`.\n"
        "- `Bugbot` means the forge's automated PR reviewer — whatever bot posts review comments on your PRs (Bugbot on Cursor; Copilot review, Devin Review, or a CI bot elsewhere). Triage it skeptically per `references/bugbot-triage.md`.\n"
        "- `Origin` is optional: where a playbook says `origin pr ...`, it applies only when the `origin` CLI exists and resolves the repo; otherwise stay on `gh`.\n"
        "- Where upstream text says `/loop`, use a scheduled wake: `rlm-heartbeat`, a goal, or a daemon schedule.\n"
        "- `skill-creator` replaces upstream's `create-skill`; `deslop`/`control-cli`/`control-ui` are upstream-plugin skills — use them if installed, else do the equivalent pass by hand.\n"
        "- Long-running program playbooks (orchestrate, autopilot-*) map best onto the factory plus resident sessions (`rlm.create_session`).\n"
    ),
}


# ---------------------------------------------------------------------------
# setup-pstack rewritten bodies
# ---------------------------------------------------------------------------

SETUP_OC = f"""# Setup pstack (opencode)

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

Write `~/.config/opencode/pstack-models.md` with a `# budget` line naming the chosen label and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Then ensure `~/.config/opencode/opencode.json` contains `\"{MODELS_FILE['opencode'].strip('`')}\"`-equivalent entry in its `instructions` array (add the file's absolute path if missing; leave any existing entries intact). Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: the role runs on the parent model (omit the task `model`). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited
feature, refactoring: {MODELS['fast_code']}
bug-fix: {MODELS['fast_code']}
perf-issue: {MODELS['fast_code']}
hillclimb: {MODELS['fast_code']}
judgment and prose: {MODELS['judgment']}
hardest tasks: {MODELS['judgment']}
how explorer: {MODELS['fast_code']}
how explainer: {MODELS['judgment']}
why investigators: {MODELS['fast_code']}
why synthesizer: {MODELS['judgment']}
reflect tooling: {MODELS['panel_third']}
reflect judgment, divergent, synthesizer: {MODELS['judgment']}
arena runners: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
arena cross-judge pool: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
swarm workers: {MODELS['fast_code']}
architect runners: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
interrogate reviewers: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
```

### 6. Confirm

Tell the user the file was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with the `create-verification-skill` skill." On yes, load and run `create-verification-skill`. On no, move on without pushing.
"""

SETUP_PRIME = f"""# Setup pstack (prime-agent)

Maintain the `pstack-models` block inside `~/.prime/agent/AGENTS.md`, the model-role table pstack skills read. The block is delimited by `<!-- pstack-models:start -->` and `<!-- pstack-models:end -->`; AGENTS.md content outside it is never touched.

## Steps

### 1. Detect available models

From the kernel, call `await rlm.find_models()` (optionally with a query per family) to list the `provider/model` selectors backed by active credentials. If you cannot detect any, ask the user for the slugs they can use. Never write a real slug you have not confirmed is available. The aliases `inherit-parent` and `auto` are always valid even though they are not detected slugs.

### 2. Load current state

The default role-to-model mapping is the table shown in step 5 below. If an existing `pstack-models` block is present in `~/.prime/agent/AGENTS.md`, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from those defaults. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer a structured choice prompt over free text. Offer these four options with these exact labels, and name the current budget when the block records one.

- `unlimited — thinking=high`
- `large — thinking=high, medium where a role tolerates it`
- `medium — thinking=medium`
- `small — thinking=low`

**(b) Apply it.** Budget maps to the `thinking` level `rlm.spawn` passes per role, and to model tier inside each family when the configured family has a cheaper member (e.g. haiku/sonnet/opus). Record the chosen thinking level on the `# budget` line; roles keep their configured model unless the budget calls for a tier change. `inherit-parent` and `auto` do not change (they omit `model`, so the child inherits the parent model, and `thinking` defaults to the parent level). If the result is not a detected slug, use the same family's detected slug nearest the target tier, else mark the role as needing a choice.

**(c) Show the roles and confirm.** Show every role with its model and thinking level, marking any real slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles. For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one child spawns per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it whose model family differs from the parent's when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen real slug is not available, stop and ask again.

### 5. Write the block

Write or replace the managed block in `~/.prime/agent/AGENTS.md` (create the file if absent). Keep content outside the markers byte-identical. Shape:

```
<!-- pstack-models:start -->
# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# `inherit-parent` or `auto` as a value: omit `model` on rlm.spawn so the child runs on the parent model. Alias entries in a panel list still count toward its fan-out.
# A value may append `; thinking=<level>` to set the spawn's reasoning level (`off|low|medium|high`).
# budget: unlimited (thinking=high)
feature, refactoring: {MODELS['fast_code']}
bug-fix: {MODELS['fast_code']}
perf-issue: {MODELS['fast_code']}
hillclimb: {MODELS['fast_code']}
judgment and prose: {MODELS['judgment']}; thinking=high
hardest tasks: {MODELS['judgment']}; thinking=high
how explorer: {MODELS['fast_code']}
how explainer: {MODELS['judgment']}
why investigators: {MODELS['fast_code']}
why synthesizer: {MODELS['judgment']}
reflect tooling: {MODELS['panel_third']}
reflect judgment, divergent, synthesizer: {MODELS['judgment']}
arena runners: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
arena cross-judge pool: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
swarm workers: {MODELS['fast_code']}
architect runners: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
interrogate reviewers: {MODELS['judgment']}, {MODELS['panel_third']}, {MODELS['fast_code']}
<!-- pstack-models:end -->
```

### 6. Confirm

Tell the user the block was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with `/skill:create-verification-skill`." On yes, invoke `/skill:create-verification-skill`. On no, move on without pushing.
"""

FILE_BODIES = {
    "opencode": {"setup-pstack": SETUP_OC},
    "prime-agent": {"setup-pstack": SETUP_PRIME},
}


# ---------------------------------------------------------------------------
# Transform pipeline
# ---------------------------------------------------------------------------

def transform_markdown(text: str, target: str, relpath: str) -> str:
    for pat, repl in common_rewrites(target):
        text = pat.sub(repl, text) if hasattr(pat, "sub") else re.sub(pat, repl, text)
    text = rewrite_slashes(text, target)
    return text


def process_skill_dir(src: Path, dst: Path, target: str) -> list[str]:
    """Copy a skill dir to dst with transforms. Returns warnings."""
    warns = []
    for item in sorted(src.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix == ".md" or item.name.endswith(".md"):
            text = item.read_text(encoding="utf-8")
            fm, body = split_frontmatter(text)
            body = transform_markdown(body, target, str(rel))
            if item.name == "SKILL.md" and rel == Path("SKILL.md"):
                new_fm = skill_frontmatter(fm, src.name, target)
                if src.name == "poteto-mode":
                    body = rewrite_poteto_mode(body, target)
                elif src.name == "setup-pstack":
                    body = FILE_BODIES[target]["setup-pstack"]
                text = dump_frontmatter(new_fm) + "\n" + body.lstrip("\n")
            else:
                text = body if not fm else dump_frontmatter(fm) + "\n" + body
            out.write_text(text, encoding="utf-8")
        elif item.name == "worktree-audit.sh":
            text = item.read_text(encoding="utf-8")
            text = text.replace(
                'transcripts="$HOME/.cursor/projects/$slug/agent-transcripts"',
                'transcripts="${PSTACK_TRANSCRIPTS_DIR:-$HOME/.cursor/projects/$slug/agent-transcripts}"'
            )
            text = text.replace(
                "# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts.",
                "# Transcripts dir defaults to Cursor's layout. Point PSTACK_TRANSCRIPTS_DIR at the\n"
                "# harness's session store instead (e.g. ~/.prime/agent/sessions for prime-agent,\n"
                "# ~/.local/share/opencode/storage for opencode)."
            )
            out.write_text(text, encoding="utf-8")
            shutil.copystat(item, out)
        else:
            shutil.copy2(item, out)
    # extra reference for prime-agent no-comments
    if target == "prime-agent" and src.name == "no-comments":
        ref = dst / "references"
        ref.mkdir(exist_ok=True)
        sicko = (SRC_AGENTS / "comment-sicko.md").read_text(encoding="utf-8")
        _, sbody = split_frontmatter(sicko)
        (ref / "comment-sicko.md").write_text(sbody, encoding="utf-8")
    return warns


def rewrite_poteto_mode(body: str, target: str) -> str:
    """Replace the Subagents section and add harness notes."""
    start = body.index("## Subagents")
    end = body.index("## Writing the reply")
    repl = POTETO_SUBAGENTS_PRIME if target == "prime-agent" else POTETO_SUBAGENTS_OC
    body = body[:start] + "## Subagents\n\n" + repl + "\n\n" + body[end:]
    # append harness notes at end
    body = body.rstrip() + "\n\n" + HARNESS_NOTE[target] + "\n"
    return body


# ---------------------------------------------------------------------------
# Static assets per target
# ---------------------------------------------------------------------------

OC_INSTALL = """#!/usr/bin/env bash
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
"""

PRIME_INSTALL = """#!/usr/bin/env bash
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
"""

OC_AGENTS = {
    "poteto.md": """---
description: poteto's agent style — playbook-routed rigorous engineering from pstack
mode: primary
---

You are in poteto mode. At the start of every task, load the `poteto-mode` skill in full — including its inline Principles index — then match the task to a playbook and copy its steps into your task list verbatim. All poteto-mode rules apply for the whole session: deliberate subagents, unslopped prose, simple code, verified work.
""",
}

OC_COMMANDS = {
    "poteto-mode.md": """---
description: Apply poteto's playbook-routed engineering style to a task
---
Load the `poteto-mode` skill in full and apply it to this task. $ARGUMENTS
""",
    "setup-pstack.md": """---
description: Configure which models pstack uses per role
---
Run the `setup-pstack` skill. $ARGUMENTS
""",
}

PRIME_PROMPTS = {
    "poteto.md": """Load and apply the `poteto-mode` skill to this request (`/skill:poteto-mode` is the explicit form): $1""",
    "setup-pstack.md": """Run the `setup-pstack` skill: $1""",
}

PRIME_PACKAGE_JSON = {
    "name": "pstack-prime-agent",
    "version": "0.15.9",
    "description": "pstack (poteto's Cursor plugin) ported to prime-agent: skills, prompts, and agent specs.",
    "license": "MIT",
    "pi": {"skills": ["skills/"], "prompts": ["prompts/"]},
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_opencode(src: Path, dst: Path) -> None:
    skills_dst = dst / "skills"
    shutil.rmtree(dst, ignore_errors=True)
    skills_dst.mkdir(parents=True)
    for d in sorted((src / "skills").iterdir()):
        if d.is_dir():
            process_skill_dir(d, skills_dst / d.name, "opencode")
    # agents
    for f in sorted((src / "agents").glob("*.md")):
        fm, body = split_frontmatter(f.read_text(encoding="utf-8"))
        body = transform_markdown(body, "opencode", f.name)
        new_fm = {"description": str(fm.get("description", f.stem)).strip().strip('"').strip("'"),
                  "mode": "subagent"}
        write(dst / "agents" / f.name, dump_frontmatter(new_fm) + "\n" + body)
    for name, content in OC_AGENTS.items():
        write(dst / "agents" / name, content)
    for name, content in OC_COMMANDS.items():
        write(dst / "commands" / name, content)
    write(dst / "install.sh", OC_INSTALL)
    (dst / "install.sh").chmod(0o755)


def build_prime(src: Path, dst: Path) -> None:
    global SRC_AGENTS
    SRC_AGENTS = src / "agents"
    shutil.rmtree(dst, ignore_errors=True)
    skills_dst = dst / "skills"
    skills_dst.mkdir(parents=True)
    for d in sorted((src / "skills").iterdir()):
        if d.is_dir():
            process_skill_dir(d, skills_dst / d.name, "prime-agent")
    for f in sorted((src / "agents").glob("*.md")):
        fm, body = split_frontmatter(f.read_text(encoding="utf-8"))
        body = transform_markdown(body, "prime-agent", f.name)
        new_fm = {"name": f.stem,
                  "description": str(fm.get("description", f.stem)).strip().strip('"').strip("'")}
        write(dst / "agents" / f.name, dump_frontmatter(new_fm) + "\n" + body)
    for name, content in PRIME_PROMPTS.items():
        write(dst / "prompts" / name, content)
    write(dst / "package.json", json.dumps(PRIME_PACKAGE_JSON, indent=2) + "\n")
    write(dst / "install.sh", PRIME_INSTALL)
    (dst / "install.sh").chmod(0o755)


def build_extras(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    for sub in ("automations", "docs"):
        s = src / sub
        if s.exists():
            shutil.copytree(s, dst / sub)
    if (src / "LICENSE").exists():
        shutil.copy2(src / "LICENSE", dst / "LICENSE")


README = """\
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
"""


ADAPTATIONS = """\
# ADAPTATIONS: Cursor → opencode / prime-agent

pstack upstream leans on Cursor-specific mechanisms. This is the mapping each
port applies.

## Invocation surface

| Cursor | opencode | prime-agent |
|---|---|---|
| Skill frontmatter `name`/`description` + extras (`icon`, `color`, `reminder`, `disable-model-invocation`, `is_background`, `mode`) | `name`, `description`, `license`, `compatibility`; extras moved to `metadata:` | `name`, `description`; `disable-model-invocation` preserved verbatim (hides from `<available_skills>`, still `/skill:name`-invocable) |
| `/skillname` slash mention in prose | `the \\`name\\` skill` (except `/poteto-mode`, `/setup-pstack` — shipped as real commands) | `` `/skill:name` `` |
| `mode: true` on poteto-mode (agent mode) | `agents/poteto.md` with `mode: primary` | not applicable — load the skill; `/poteto` prompt provided |
| `~/.cursor/rules/pstack-models.mdc` always-applied rule | `~/.config/opencode/pstack-models.md` + `instructions` entry in `opencode.json` | `<!-- pstack-models:start -->` block in `~/.prime/agent/AGENTS.md` |
| Cursor model slugs (`grok-4.7-xhigh-fast`, `claude-opus-5-5-max`, `gpt-5.6-sol-max`; effort suffixes `-max`/`-xhigh`/`-high`/`-medium`/`-low`, `-fast`) | per-role defaults: fast code `anthropic/claude-haiku-4-5`, judgment/prose `anthropic/claude-opus-4-5`, third panel `openai/gpt-5.1` | same defaults; effort ladder maps to `thinking` levels `off`/`low`/`medium`/`high` on `rlm.spawn` |

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
| transcripts at `~/.cursor/projects/<slug>/agent-transcripts/` | harness session store (`~/.local/share/opencode/storage/`) | `~/.prime/agent/sessions/` |

## Externals kept conditional

- `deslop`, `control-cli`, `control-ui` (upstream `cursor-team-kit` skills) — named
  with "(upstream Cursor plugin — if installed)" and each call site keeps a
  hand-pass fallback, matching upstream's own optional posture.
- `create-skill` (Cursor built-in) → opencode: the `authoring-a-skill` playbook
  conventions; prime-agent: `skill-creator`.
- `Origin` forge CLI references are left verbatim — upstream already gates them
  on `command -v origin`, with `gh` fallback.
- `benny` automation pack + docs guide are in `extras/` verbatim (Cursor-shaped
  by design).
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/home/ubuntu/cursor-plugins/pstack")
    ap.add_argument("--dst", default="/home/ubuntu/pstack-ports")
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    build_opencode(src, dst / "opencode")
    build_prime(src, dst / "prime-agent")
    build_extras(src, dst / "extras")
    write(dst / "README.md", README)
    write(dst / "ADAPTATIONS.md", ADAPTATIONS)
    print("wrote", dst)


if __name__ == "__main__":
    main()
