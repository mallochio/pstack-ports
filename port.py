#!/usr/bin/env python3
"""Port pstack (Cursor plugin by poteto/Lauren Tan) to other agent harnesses.

Reads the upstream pstack tree and emits:

  opencode/      skills + agents + commands installable into ~/.config/opencode/
  prime-agent/   a prime-agent package (skills + prompts + agent specs),
                 installable via ~/.prime/agent/ copies or a `packages` entry
  extras/        upstream docs + automations, verbatim
  UPSTREAM       stamp of the exact upstream commit the outputs were built from

Usage:
  python3 port.py --fetch            # download latest upstream pstack, rebuild
  python3 port.py --fetch --ref vX   # pin to a ref/tag/sha
  python3 port.py --src DIR          # build from a local checkout instead
  python3 port.py --check            # regenerate to a temp dir and report drift

Re-runnable; output dirs are regenerated wholesale. Files under
overrides/<target>/ are copied over the generated output last, so local
patches survive an upstream re-port (see README).
"""

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    sys.exit("port.py needs pyyaml: uv run --with pyyaml python3 port.py ...")

WARNINGS: list[str] = []


def warn(msg: str) -> None:
    """Print a build warning and remember it so --strict can fail the run."""
    WARNINGS.append(msg)
    print(f"warning: {msg}", file=sys.stderr)


UPSTREAM_REPO = "cursor/plugins"
UPSTREAM_SUBDIR = "pstack"

# ---------------------------------------------------------------------------
# Per-harness constants
# ---------------------------------------------------------------------------

# Default role models. These are EXAMPLES ONLY for the setup-pstack skill's
# budget-tier illustration. Generated skill bodies default every role to
# `inherit-parent` (omit `model`) so the ports work with zero hard-coded slugs:
# the user defines per-role models via setup-pstack, or everything simply runs
# on the parent model. Never write a concrete slug into a skill body default.
MODELS = {
    "fast_code": "anthropic/claude-haiku-4-5",
    "judgment": "anthropic/claude-opus-4-5",
    "panel_third": "openai/gpt-5.1",
}

# Legacy exact-map of known Cursor slugs. Kept for reference; the live rewrite
# uses effort-ladder REGEXES below so new upstream suffixes (-xhigh/-high/…)
# can never leak through. All Cursor slugs map to `inherit-parent`.
CURSOR_SLUGS = {
    "grok-4.7-xhigh-fast": "inherit-parent",
    "grok-4.7-medium-fast": "inherit-parent",
    "claude-opus-5-5-max": "inherit-parent",
    "claude-opus-5-5-medium": "inherit-parent",
    "gpt-5.6-sol-max": "inherit-parent",
}

MODELS_FILE = {
    "opencode": "`~/.config/opencode/pstack-models.md`",
    "prime-agent": "`~/.prime/agent/pstack-models.md`",
}
MODELS_BASENAME = {
    "opencode": "pstack-models.md",
    "prime-agent": "pstack-models.md",
}

# Skills that exist inside pstack (so `/name` means "the name skill").
PSTACK_SKILLS = {
    "architect",
    "arena",
    "automate-me",
    "benchmark-checklist",
    "blast-radius",
    "bro",
    "correct",
    "create-verification-skill",
    "figure-it-out",
    "how",
    "interrogate",
    "maintain-verification-skill",
    "make-bot-ui",
    "no-comments",
    "poteto-mode",
    "recall",
    "reflect",
    "setup-pstack",
    "show-me-your-work",
    "swarm",
    "tdd",
    "teach",
    "technical-writing",
    "typescript-best-practices",
    "unslop",
    "why",
}

# Skills owned by other Cursor plugins / built-ins we reference conditionally.
EXTERNAL_SKILLS = {"deslop", "control-cli", "control-ui", "create-skill", "babysit"}

ALL_SLASH = PSTACK_SKILLS | EXTERNAL_SKILLS | {"no-comments"}

# Populated at build time from the source tree's skills/ dir so new upstream
# skills are recognized even though the static PSTACK_SKILLS set predates them.
ACTIVE_SKILLS: set = set()


def slash_repl(target: str, name: str) -> str:
    if target == "prime-agent":
        return f"`/skill:{name}`"
    # opencode: COMMAND_SKILLS ship as real /name commands.
    if name in COMMAND_SKILLS:
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
        AGENT_DESC_PERSISTENT,
        *PERSISTENT_PLAYBOOK_REWRITES,
        # ---- model config file -------------------------------------------------
        # Both harnesses use a standalone file. Prime keeps only a pointer
        # block in AGENTS.md so pstack stays removable without touching the
        # rest of that file.
        (r"`~/\.cursor/rules/pstack-models\.mdc`", "`pstack-models.md`"),
        (
            re.escape("~/.cursor/rules/pstack-models.mdc"),
            "~/.config/opencode/pstack-models.md"
            if not is_prime
            else "~/.prime/agent/pstack-models.md",
        ),
        (r"`pstack-models\.mdc`", "`pstack-models.md`"),
        (r"pstack-models\.mdc", "pstack-models.md"),
        # ---- transcripts -------------------------------------------------------
        (
            r"`~/.cursor/projects/<slug>/agent-transcripts/<uuid>/<uuid>\.jsonl`[^.]*"
            r"(where `<slug>` is the workspace path with the leading slash dropped and each \"/\" turned into \"-\" \(so `/Users/you/proj` becomes `Users-you-proj`\))?\.\s*Every line is one chat message\.",
            (
                "the session transcript for this workspace under prime-agent's session "
                "store (`~/.prime/agent/sessions/*.jsonl`). Every line is one session entry."
            )
            if is_prime
            else (
                "the session transcript for this workspace under the harness's session "
                "store (opencode keeps sessions under `~/.local/share/opencode/storage/`; "
                "locate this session's file rather than globbing every workspace)."
            ),
        ),
        (
            r"the active workspace's `agent-transcripts/` directory \(the system prompt names the path\. Do not glob across `~/.cursor/projects/\*/`, that crosses workspace boundaries and reads private chats from unrelated projects\)",
            "this session's transcript directory (do not glob across "
            "`~/.prime/agent/sessions/` entries outside this session — that crosses "
            "workspace boundaries and reads private chats from unrelated projects)"
            if is_prime
            else "this session's transcript directory (do not glob across the harness's "
            "other workspaces' session stores — that crosses workspace boundaries "
            "and reads private chats from unrelated projects)",
        ),
        (
            r"a cloud-agent URL",
            "a resident-session URL" if is_prime else "a background-session URL",
        ),
        (
            r"the active workspace's `agent-transcripts/` directory",
            "this session's transcript directory"
            if not is_prime
            else "`~/.prime/agent/sessions/`",
        ),
        (
            r"local transcripts under `agent-transcripts/`",
            "local session transcripts under `~/.prime/agent/sessions/`"
            if is_prime
            else "local transcripts under the harness's session store",
        ),
        (
            r"`agent-transcripts/`",
            "`~/.prime/agent/sessions/`" if is_prime else "the harness's session store",
        ),
        (
            r"~/.cursor/projects/\*/",
            "the harness's other workspaces' session stores"
            if not is_prime
            else "~/.prime/agent/sessions/ entries outside this session",
        ),
        (
            r"the Cursor dashboard",
            "the harness's session list"
            if not is_prime
            else "the daemon's session list",
        ),
        (
            r"After a Cursor restart: local agents are dead, cloud work is not\.",
            "After a harness restart: spawned children are dead, resident sessions are not."
            if is_prime
            else "After a harness restart: running subagents are dead, detached sessions are not.",
        ),
        (r"Re-read the standing orders", "Re-read the standing orders"),
        (r"reattach cloud work", "reattach detached work"),
        (r"a Cursor restart", "a harness restart"),
        (
            r"`git show origin/main:pstack/(skills/[^`]+)`",
            lambda m: "read the installed copy at `{}`".format(
                ("~/.prime/agent/" if is_prime else "~/.config/opencode/") + m.group(1)
            ),
        ),
        (
            r"`git show origin/main:<control skill path>`",
            "read the installed control skill (if one is installed)",
        ),
        # ---- make-bot-ui: automation-host agnostic ----------------------------
        (
            r"Call `update_state` with target `routine` and action `create`\. Set these fields:",
            "Create a webhook routine with the automation host this harness runs (Cursor routines, "
            "a webhook responder, or a scheduled-agent inbox — whichever you configured for benny). "
            "Set these fields:",
        ),
        (
            r"If `update_state` shows a confirm card, wait for the user to confirm\.",
            "If the automation host asks for confirmation, wait for the user to confirm.",
        ),
        (
            r"1\. Click this agent's name in the chat header, or press \*\*Cmd\+Shift\+I\*\*\.\n"
            r"2\. Find the \*\*Routines\*\* list under the computer preview\.\n"
            r"3\. Open this webhook routine\.\n"
            r"4\. Copy the webhook URL\. The user may paste the URL in chat\.\n"
            r"5\. Copy the sender key\. The user must not paste the sender key in chat\.",
            "1. Open the routine in the automation host's panel/UI.\n"
            "2. Copy the webhook URL. The user may paste the URL in chat.\n"
            "3. Copy the sender key. The user must not paste the sender key in chat.",
        ),
        (
            r"The URL looks like `https://api2\.cursor\.sh/automations/webhook/<id>` with no query string\.",
            "On Cursor routines the URL looks like "
            "`https://api2.cursor.sh/automations/webhook/<id>` with no query string; "
            "other automation hosts expose an equivalent URL.",
        ),
        # ---- skills dirs --------------------------------------------------------
        (
            r"\.cursor/skills/",
            ".opencode/skills/" if not is_prime else ".prime/agent/skills/",
        ),
        (
            r"~/.cursor/skills/",
            "~/.config/opencode/skills/" if not is_prime else "~/.prime/agent/skills/",
        ),
        (
            r"~/.cursor/plugins/",
            "~/.config/opencode/"
            if not is_prime
            else "installed package paths under `~/.prime/agent/`",
        ),
        (
            r"the Cursor environment\. Use the available-tools map when present\. Otherwise inspect the `mcps/` directory Cursor exposes for enabled MCP servers\.",
            (
                "the harness environment. Use the available-tools map when present, or "
                "the configured MCP server list in opencode.json."
            )
            if not is_prime
            else (
                "the harness environment through the kernel (`rlm.mcp` / `mcp_status`)."
            ),
        ),
        # ---- cursor-team-kit / built-in externals --------------------------------
        (
            r"a slop-strip \(the `deslop` skill from the `cursor-team-kit` plugin \(`/deslop`\)\)",
            "a slop-strip pass (the `deslop` skill if your setup provides it, else a "
            "hand pass against the **unslop** checklist)",
        ),
        (
            r"the `deslop` skill from the `cursor-team-kit` plugin \(`/deslop`\)",
            "a `deslop` slop-strip if your setup provides it, else a hand pass "
            "against the **unslop** checklist",
        ),
        (
            r"`cursor-team-kit` publishes `control-cli` \(CLIs and TUIs\) and `control-ui` \(browser / Electron / web UIs\)\.?",
            "Use `control-cli` for CLIs/TUIs or `control-ui` for browser/web UIs when your "
            "setup ships them; otherwise drive the real surface directly.",
        ),
        (
            r"the matching control skill \(such as `control-ui` or `control-cli` from `cursor-team-kit`\)",
            "the matching control-surface skill (`control-ui`/`control-cli` upstream — "
            "if installed, else drive the real surface directly)",
        ),
        (
            r"\(such as `control-ui` or `control-cli`[^)]*\)",
            "(a control-surface skill such as `control-ui`/`control-cli` if installed, "
            "else the real surface directly)",
        ),
        (
            r"(`control-ui` or `control-cli`) from `cursor-team-kit`",
            r"\1 (upstream Cursor plugin — if installed)",
        ),
        (
            r"(`control-(ui|cli)`) from `cursor-team-kit`",
            r"\1 (upstream Cursor plugin — if installed)",
        ),
        (
            r"(`?/?control-(ui|cli)`?|/control-(ui|cli)) from `cursor-team-kit`",
            "a control-surface skill (upstream Cursor plugin — if installed)",
        ),
        (
            r"\(from `cursor-team-kit`\)",
            "(upstream Cursor plugin — use it if installed, else do the pass by hand)",
        ),
        (
            r" from `cursor-team-kit`| from the `cursor-team-kit` plugin",
            " (upstream Cursor plugin — use it if installed, else do the pass by hand)",
        ),
        (r" of `cursor-team-kit`", " of the upstream Cursor plugin"),
        (r"`cursor-team-kit`", "the upstream Cursor plugin"),
        (
            r"the matching control skill",
            "the matching control-surface skill when one is installed, else the real surface directly",
        ),
        (
            r"the \*\*create-skill\*\* skill \(Cursor's built-in for authoring SKILL\.md files\)",
            "the `skill-creator` skill"
            if is_prime
            else "the `authoring-a-skill` playbook's conventions",
        ),
        (
            r"Cursor's built-in `create-skill` \(authoring\)",
            "prime-agent's built-in `skill-creator` (authoring)"
            if is_prime
            else "the `authoring-a-skill` playbook (authoring)",
        ),
        (
            r"Cursor's built-in `create-skill` skill",
            "the `skill-creator` skill"
            if is_prime
            else "the `authoring-a-skill` playbook",
        ),
        (
            r", and not Cursor's built-in babysit skill, whose description matches the same words",
            ", and not any built-in babysit helper",
        ),
        (r"Cursor's built-in babysit skill", "a built-in babysit helper"),
        # ---- subagent mechanics --------------------------------------------------
        (
            r"Substituting\s+`generalPurpose`\s+skips",
            "Substituting a plain child skips",
        ),
        (
            r"`?subagent_type`?: ?`?\"?generalPurpose\"?`?",
            "a plain `rlm.spawn` child" if is_prime else "the `general` subagent",
        ),
        # bare `generalPurpose` with no subagent_type prefix (e.g. why skill)
        (
            r"is `generalPurpose` with",
            "is a plain `rlm.spawn` child with"
            if is_prime
            else "is the `general` subagent with",
        ),
        (
            r"`generalPurpose`",
            "a plain child" if is_prime else "the `general` subagent",
        ),
        # dedupe spawns rewritten from "Task subagent with subagent_type: X"
        (
            r"Every spawn is a Task subagent with the `general` subagent and",
            "Every spawn uses the `general` subagent with",
        ),
        (
            r"Every spawn is a Task subagent with a plain `rlm\.spawn` child and",
            "Every spawn is a plain `rlm.spawn` child with",
        ),
        (
            r"Every spawn is a RLM child with a plain `rlm\.spawn` child and",
            "Every spawn is a plain `rlm.spawn` child with",
        ),
        (
            r"`subagent_type: ?\"poteto-agent\"`|subagent_type: \"poteto-agent\"",
            "an `rlm.spawn` child whose prompt opens with the `poteto-agent` spec paragraph"
            if is_prime
            else "the `poteto-agent` subagent",
        ),
        (
            r"`subagent_type: \"Comment Sicko\"`|subagent_type: \"Comment Sicko\"",
            "an `rlm.spawn` child whose prompt is `references/comment-sicko.md`"
            if is_prime
            else "the `comment-sicko` subagent",
        ),
        # lowercase variant used by no-comments skill: valid opencode
        # subagent_type, but meaningless to rlm.spawn — inline the spec instead.
        (
            r"`subagent_type: \"comment-sicko\"`|subagent_type: \"comment-sicko\"",
            "an `rlm.spawn` child whose prompt is `references/comment-sicko.md`"
            if is_prime
            else '`subagent_type: "comment-sicko"`',
        ),
        (r"`subagent_type`:", "child spec:" if is_prime else "`subagent_type`:"),
        (
            r"Spawn one Task subagent",
            "Spawn one %s" % ("`rlm.spawn` child" if is_prime else "Task subagent"),
        ),
        (
            r"one Task subagent",
            "one %s" % ("`rlm.spawn` child" if is_prime else "Task subagent"),
        ),
        (r"Task subagent", "RLM child" if is_prime else "Task subagent"),
        (
            r"full Task schema including `environment`",
            "full `rlm.spawn` signature" if is_prime else "full `task` schema",
        ),
        (r"the Task tool", "the `rlm.spawn` API" if is_prime else "the `task` tool"),
        (r"(?<!the )Task tool", "`rlm.spawn` API" if is_prime else "`task` tool"),
        (
            r"one `Task` call",
            "one %s" % ("`rlm.spawn` call" if is_prime else "`task` call"),
        ),
        (
            r"three `Task` calls",
            "three %s" % ("`rlm.spawn` calls" if is_prime else "`task` calls"),
        ),
        (
            r"single message using the Task tool",
            "single fan-out of `rlm.spawn` calls"
            if is_prime
            else "single message using the Task tool",
        ),
        (r"`Task` calls", "`rlm.spawn` calls" if is_prime else "`task` calls"),
        (r"`Task` call", "`rlm.spawn` call" if is_prime else "`task` call"),
        (r"\bTask calls\b", "`rlm.spawn` calls" if is_prime else "task calls"),
        (r"`Task`", "`rlm.spawn`" if is_prime else "`task`"),
        (r"\bTask\b(?= with)", "`rlm.spawn`" if is_prime else "`task`"),
        (r"\bTask\b(?= spawn)", "`rlm.spawn` child" if is_prime else "`task`"),
        (
            r"\bevery Task spawn\b",
            "every `rlm.spawn` child" if is_prime else "every task spawn",
        ),
        (
            r"Spawn `Task`",
            "Spawn %s" % ("an `rlm.spawn` child" if is_prime else "a `task`"),
        ),
        # run_in_background
        (
            r"`run_in_background: true`",
            "children run concurrently once spawned — collect them with `rlm.collect`"
            if is_prime
            else "run in the background where the task tool supports it",
        ),
        (
            r"run_in_background: true",
            "concurrent children" if is_prime else "background tasks",
        ),
        # agent mode / readonly (list forms AND inline backticked forms)
        (
            r"agent mode \(`readonly: false`\)|agent mode \(readonly strips MCP\)",
            "full tool access",
        ),
        (r"`readonly: (?:true|false)` \(agent mode\)", "full tool access"),
        (r"with `readonly: false` \(agent mode\)", "with full tool access"),
        (
            r"`readonly`: `true`",
            "a read-only brief: instruct the child it must not modify files or run "
            "mutating commands",
        ),
        (r"`readonly`: `false` \(agent mode\)\.", "full tool access."),
        # colon-inside-backticks inline forms (how/why/interrogate upstream)
        (r"`readonly: (?:true|false)` \(agent mode\)", "full tool access"),
        (r"with `readonly: false` \(agent mode\)", "with full tool access"),
        (
            r"`readonly: true`",
            "a read-only brief: instruct the child it must not modify files or run "
            "mutating commands",
        ),
        (r"`readonly: false`", "full tool access"),
        (
            r"- `readonly`: `true`",
            "- read-only brief: instruct the child it must not modify files or run "
            "mutating commands",
        ),
        (r"- `readonly`: `false` \(agent mode\)\.", "- tool access: full."),
        (r"readonly/Ask mode", "a read-only tool restriction"),
        (r"Readonly/Ask mode", "A read-only tool restriction"),
        (
            r"readonly strips MCPs?",
            "read-only modes strip MCPs" if is_prime else "denied tools strip MCPs",
        ),
        (
            r"Readonly strips MCPs",
            "Read-only modes strip MCPs" if is_prime else "Denied tools strip MCPs",
        ),
        (
            r"one readonly judge subagent",
            "one read-only judge child" if is_prime else "one read-only judge subagent",
        ),
        (
            r"Always `environment: \"cloud\"` unless the task needs this machine",
            "Always a spawned child (a daemon session via `rlm.create_session` when the work must outlive this session) unless the task needs this machine"
            if is_prime
            else "Always a detached background subagent session unless the task needs this machine",
        ),
        (r"`environment: \"cloud\"`, ", ""),
        (
            r"`environment: \"cloud\"`",
            "a resident/daemon child" if is_prime else "a detached session",
        ),
        (
            r"`environment: \"local\"`",
            "a child on this machine" if is_prime else "a local task",
        ),
        (
            r"Cursor cloud agent",
            "resident depth-0 session (`rlm.create_session`)"
            if is_prime
            else "dedicated background subagent session",
        ),
        # cloud workers / spawns / topology (swarm, orchestrate, multi-phase-plan)
        (r"Fan out N parallel cloud workers\.", "Fan out N parallel workers."),
        (r"parallel cloud workers", "parallel workers"),
        (
            r"N is total workers, not the cloud concurrency limit\.",
            "N is total workers, not a harness concurrency limit.",
        ),
        (
            r"Use a local task only when the worker needs access to something on the user's computer\.",
            "Stay on this machine only when the worker needs local access; otherwise spawn normally.",
        ),
        (
            r"When a worker must start from a non-default pushed branch, pass `cloud_base_branch`\.",
            "When a worker must start from a non-default pushed branch, start it on that branch if the harness supports it; otherwise say so and stay on the default branch.",
        ),
        (
            r"`cloud_base_branch`",
            "a base-branch override the harness supports (else the default branch)",
        ),
        (
            r"Verbatim paste is for cloud spawns and every resume\.",
            "Verbatim paste is for cross-session handoffs and every resume.",
        ),
        (
            r"cloud spawns",
            "cross-session handoffs" if not is_prime else "daemon-session handoffs",
        ),
        (
            r"its spawn budget with the cloud default and the local exception list",
            "its spawn budget with the default and the exception list",
        ),
        (
            r"Chain rebases run in cloud\.",
            "Keep chain rebases serialized on one worker.",
        ),
        (
            r"Each live lane runs on its own cloud VM",
            "Each live lane runs in its own worker/session",
        ),
        (r"cloud VM", "worker" if not is_prime else "session"),
        (r"cloud agents", "resident sessions" if is_prime else "background sessions"),
        (r"cloud agent", "resident session" if is_prime else "background session"),
        (r"Cloud agents", "Resident sessions" if is_prime else "Background sessions"),
        (r"Cloud agent", "Resident session" if is_prime else "Background session"),
        (
            r"in local and cloud roots",
            "in interactive and daemon sessions"
            if is_prime
            else "in local and background sessions",
        ),
        # ---- AskQuestion ----------------------------------------------------------
        (
            r"Prefer `?AskQuestion`? over free text\.",
            "Prefer a structured choice prompt over free text where the UI offers one.",
        ),
        (
            r"confirm intent with `AskQuestion`",
            "confirm intent with a structured question",
        ),
        (
            r"Use the `AskQuestion` tool \(structured multi-choice\) rather than asking the user to type from scratch\.",
            "Use a structured multi-choice question where the harness UI offers one rather "
            "than asking the user to type from scratch.",
        ),
        (r"About to `AskQuestion`", "About to ask the human a structured question"),
        (r"`AskQuestion`", "a structured question"),
        (r"AskQuestion", "a structured question"),
        # ---- poteto-agent lookup: no .cursor-plugin dir on these harnesses ----
        (
            r"If more than one file matches, use the copy whose `\.cursor-plugin/plugin\.json` has the highest `version`\.",
            "If more than one file matches, use the copy under the active config directory.",
        ),
        # ---- make-bot-ui secret handoff: Cursor-only card ----------------------
        (
            r"Do not accept the sender key in chat\. Send a secret-request, then stop\. That card is the whole turn\.",
            "Do not accept the sender key in chat. The secret-request card is Cursor-only — pause and ask the user to store the key in the automation host out of band, then continue.",
        ),
        (
            r"type: secret-request",
            "type: secret-request (Cursor-only — collect the key out of band on other harnesses)",
        ),
        (r"SendToUser\n", "SendToUser (Cursor-only card)\n"),
        (
            r"The wake is a `\[routine\]` turn for that webhook routine\. It includes a `<webhook_event>` block with `headers` \(`content-type`, `user-agent`\), `body_digest` \(sha256\), `body`, and `timestamp_ms`\.",
            "The wake is a webhook-triggered turn for that routine. On Cursor routines it includes a `<webhook_event>` block with `headers`, `body_digest` (sha256), `body`, and `timestamp_ms`; other hosts expose an equivalent payload.",
        ),
        # ---- reflect transcript listing placeholder ----------------------------
        (
            r"ls -t <agent-transcripts>/\*\.jsonl <agent-transcripts>/\*/\*\.jsonl <agent-transcripts>/\*/subagents/\*\.jsonl 2>/dev/null \| head -10",
            "list recent session files in this session's transcript directory (see the harness notes for the store path)",
        ),
        # ---- poteto-help upstream disclaimer (ported verbatim upstream) --------
        (
            r"pstack is built for Cursor\. Its skills use the Agent Skills format, so other tools can read them\. But most workflow skills, including `/poteto-mode`, `/how`, `/why`, and `/teach`, spawn Cursor subagents with per-role models, and Custom Modes and `/loop` are Cursor features, so those parts may not work there\.",
            (
                "pstack upstream is built for Cursor. This port already maps its subagent, model, and wake mechanics onto prime-agent (`rlm.spawn`, `pstack-models.md`, scheduled wakes), so the workflow skills work here. Custom Modes remain Cursor-only — invoke `/skill:poteto-mode` per task."
            )
            if is_prime
            else (
                "pstack upstream is built for Cursor. This port already maps its subagent, model, and wake mechanics onto opencode (the `task` tool, `pstack-models.md`, background watchers), so the workflow skills work here. Custom Modes remain Cursor-only — start each task with `/poteto-mode`."
            ),
        ),
        # ---- /loop ---------------------------------------------------------------
        (
            r"Cursor's `/loop` command \(a built-in, not a pstack skill\)",
            "a heartbeat/scheduled wake (goals, schedules, `rlm-heartbeat`)"
            if is_prime
            else "a re-armed wake (a background watcher task or repeated checks)",
        ),
        (
            r"Cursor's `/loop` command",
            "a heartbeat/scheduled wake" if is_prime else "a re-armed wake",
        ),
        (
            r"Run `drive` and `background` under `/loop` in dynamic mode\.",
            "Keep `drive` and `background` alive on a heartbeat or scheduled wake."
            if is_prime
            else "Keep `drive` and `background` alive on a re-armed wake.",
        ),
        (
            r"Hold the watch under `/loop` in dynamic mode\.",
            "Hold the watch on a scheduled wake."
            if is_prime
            else "Hold the watch on a re-armed wake.",
        ),
        (
            r"arm `/loop 1h` with a prompt that runs this tick",
            "arm a 1-hour heartbeat/scheduled wake whose prompt runs this tick"
            if is_prime
            else "arm a 1-hour re-wake (background task) whose prompt runs this tick",
        ),
        (
            r"`/loop` works in local and cloud roots\.",
            "Scheduled wakes work in interactive and daemon sessions."
            if is_prime
            else "Re-wakes work in interactive and background sessions.",
        ),
        (
            r"`/loop` per component until the diff is zero\.",
            "Re-check each component on a periodic wake until the diff is zero.",
        ),
        (r"/loop 1h", "a 1h periodic wake" if not is_prime else "a 1h scheduled wake"),
        (r"`/loop`", "a periodic wake" if not is_prime else "a scheduled wake"),
        (r"/loop\b", "a periodic wake" if not is_prime else "a scheduled wake"),
        # ---- poteto-help built-ins line ---------------------------------------
        (
            r"`/loop` and `/create-skill` are Cursor built-ins\.",
            ("a scheduled wake and `skill-creator` are the harness equivalents.")
            if is_prime
            else "a periodic wake and the `authoring-a-skill` playbook conventions are the harness equivalents.",
        ),
        # ---- model slugs: effort-ladder REGEXES (never leak new suffixes) -----
        # Cursor slugs embed effort (-max/-xhigh/-high/-medium/-low, -fast).
        # Ports are slug-free: every role defaults to `inherit-parent`.
        (r"`grok-4\.7-(?:xhigh|high|medium|low)(?:-fast)?`", "`inherit-parent`"),
        (r"`claude-opus-5-5-(?:max|xhigh|high|medium|low)`", "`inherit-parent`"),
        (r"`gpt-5\.6-sol-max`", "`inherit-parent`"),
        (r"\bgrok-4\.7-(?:xhigh|high|medium|low)(?:-fast)?\b", "inherit-parent"),
        (r"\bclaude-opus-5-5-(?:max|xhigh|high|medium|low)\b", "inherit-parent"),
        # own example slugs must never survive as skill-body defaults either
        (r"`anthropic/claude-(?:haiku|sonnet|opus)-4-5`", "`inherit-parent`"),
        (r"`openai/gpt-5\.1`", "`inherit-parent`"),
        # panel/family fallback prose: Cursor families mean nothing here
        (
            r"Families go by prefix: `claude-\*`, and `grok-\*` with `cursor-grok-\*` and `composer-\*`\.?",
            "Families go by the role line; unlisted families fall back to the parent model.",
        ),
        (
            r"A refused `composer-\*` entry runs on (`anthropic/claude-haiku-4-5`|Reviewer B's default|its family's default|`grok-4\.7-xhigh-fast`|`inherit-parent`)\.",
            "A refused entry runs on the parent model.",
        ),
        (
            r"A refused `composer-\*` entry runs on [^.\n]*\.",
            "A refused entry runs on the parent model.",
        ),
        (
            r"If the `task` tool rejects a slug, use the default and say so\.",
            "If the `task` tool rejects a role value, omit `model` so that seat runs on the parent model and say so.",
        ),
        (
            r"If the `rlm\.spawn` API rejects a slug, use the default and say so\.",
            "If the `rlm.spawn` API rejects a role value, omit `model` so that seat runs on the parent model and say so.",
        ),
        (
            r"If it rejects the default, use the closest valid slug of the same family from its error message\.",
            "If it rejects that, omit `model` so it runs on the parent model and say so.",
        ),
        (r" and fast or standard speed", ""),
        (
            r"A refused `composer-\*` entry runs on `grok-4\.7-xhigh-fast`\.",
            "A refused entry runs on the parent model.",
        ),
        (
            r"With no family match, use `claude-opus-5-5-xhigh`\.?",
            "With no family match, use the parent model.",
        ),
        (
            r"With no family match, use `inherit-parent`\.?",
            "With no family match, use the parent model.",
        ),
        (
            r"If it rejects a default, use the closest valid slug of the same family from its error message\.",
            "If it rejects the role value, omit `model` so the seat runs on the parent model and say so.",
        ),
        (
            r"If the (`task`|`` `rlm\.spawn` ``|`rlm\.spawn` API|Task) tool rejects a configured entry, run that (seat|reviewer) on its family's (table default|default) and say so\.",
            "If the spawn API rejects a configured entry, omit `model` so that seat runs on the parent model and say so.",
        ),
        (
            r"If the `task` tool rejects a configured entry, run that seat on its family's default and say so\.",
            "If the `task` tool rejects a configured entry, omit `model` so that seat runs on the parent model and say so.",
        ),
        (
            r"If the `rlm\.spawn` API rejects a configured entry, run that seat on its family's default and say so\.",
            "If the `rlm.spawn` API rejects a configured entry, omit `model` so that seat runs on the parent model and say so.",
        ),
        (
            r"If the `task` tool rejects a configured entry, run that reviewer on its family's table default and say so\.",
            "If the `task` tool rejects a configured entry, omit `model` so that reviewer runs on the parent model and say so.",
        ),
        (
            r"If the `rlm\.spawn` API rejects a configured entry, run that reviewer on its family's table default and say so\.",
            "If the `rlm.spawn` API rejects a configured entry, omit `model` so that reviewer runs on the parent model and say so.",
        ),
        (
            r"spawn with the closest equivalent from the valid slugs in its error message \(prefer the same family and reasoning tier\), and open a separate PR to update the default table\. Do not block the (review|work) on the slug issue\.",
            "omit `model` so it runs on the parent model and continue. Do not block the \\1 on the model issue.",
        ),
        (
            r"., and open a separate PR to update the default table\. Do not block the review on the slug issue\.",
            " and continue; do not block the review on the model issue.",
        ),
        (
            r"default to one each on `inherit-parent` and `inherit-parent`",
            "default to `inherit-parent` for each seat (omit `model`)",
        ),
        (
            r"choose from `inherit-parent` and `inherit-parent`",
            "use `inherit-parent` (omit `model`) unless a role line sets models",
        ),
        (
            r"use `inherit-parent` and `inherit-parent`",
            "use `inherit-parent` for each runner (omit `model`)",
        ),
        (
            r"Pick each subagent's reasoning level and fast or standard speed for the job, per the Subagents section of the \*\*poteto-mode\*\* skill\.",
            "Pick each subagent's thinking/reasoning level for the job, per the Subagents section of the **poteto-mode** skill.",
        ),
        # exact legacy maps (kept last so regexes above win)
        *[(re.escape(k), v) for k, v in CURSOR_SLUGS.items()],
        # ---- generic create-skill fallback (after specific sentence rules) ----
        (
            r"`create-skill`",
            "`skill-creator`" if is_prime else "the `authoring-a-skill` playbook",
        ),
        (r"\bcreate-skill\b", "skill-creator" if is_prime else "authoring-a-skill"),
        # ---- forge/watch ------------------------------------------------------------
        (
            r"re-read this playbook from trunk with `git show origin/main:pstack/",
            "re-read this playbook from your installed pstack copy at `",
        ),
    ]
    return rules


# Slash-mention rewrite, applied after common rules so special sentences win.
def rewrite_slashes(text: str, target: str) -> str:
    # ACTIVE_SKILLS is the live skill-dir list from the source tree (falls back
    # to the static PSTACK_SKILLS snapshot), so new upstream skills get
    # /skill:-style rewrites without edits here.
    skill_names = ACTIVE_SKILLS or PSTACK_SKILLS
    names = "|".join(
        sorted(re.escape(n) for n in (ALL_SLASH - PSTACK_SKILLS) | skill_names)
    )
    # 1. Exact backticked `/skill`: consume both backticks so replacements never nest.
    pat_bt = re.compile(r"`/(" + names + r")`(?!\w)")
    # 2. Bare `/skill` (including inside a longer backticked span like
    #    `/poteto-mode do X`): leave surrounding backticks intact, strip outer
    #    backticks from the replacement if it lands inside an open span.
    pat_bare = re.compile(r"(?<![\w.:/-])/(" + names + r")\b(?![\w-])")

    def _sub_bt(m: re.Match) -> str:
        return slash_repl(target, m.group(1))

    text = pat_bt.sub(_sub_bt, text)

    # Track backtick parity per line so a bare /skill inside `...` doesn't
    # inject inner backticks that break the span.
    out_lines = []
    for line in text.splitlines(keepends=True):

        def _sub_bare(m: re.Match, _line: str = line) -> str:
            rep = slash_repl(target, m.group(1))
            before = _line[: m.start()]
            if before.count("`") % 2 == 1:
                return rep.replace("`", "")
            return rep

        out_lines.append(pat_bare.sub(_sub_bare, line))
    return "".join(out_lines)


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)), text[m.end() :]


def dump_frontmatter(fm: dict) -> str:
    return (
        "---\n"
        + yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip()
        + "\n---\n"
    )


# Skills directly invocable by the user. Everything else is gated behind
# poteto-mode: it loads gated skills explicitly per playbook, and harnesses
# must not trigger them autonomously.
ENTRY_SKILLS = {"poteto-mode", "setup-pstack", "poteto-help"}

# Description prefix marking a skill as poteto-mode-gated. Flags alone are not
# honored by every harness, so the description itself carries the gate.
GATE_PREFIX = "Only use when poteto-mode is active for this task. "


def skill_frontmatter(fm: dict, dirname: str, target: str) -> dict:
    out: dict = {"name": dirname}
    gated = dirname not in ENTRY_SKILLS
    if "description" in fm:
        d = str(fm["description"]).strip().strip('"').strip("'")
        d = rewrite_slashes(d, target)
        # descriptions bypass transform_markdown: apply the harness-specific
        # authoring-skill name so frontmatter never routes to Cursor's tool.
        if target == "prime-agent":
            d = re.sub(r"`create-skill`", "`skill-creator`", d)
            d = re.sub(r"\bcreate-skill\b", "skill-creator", d)
        else:
            d = re.sub(r"`create-skill`", "the `authoring-a-skill` playbook", d)
            d = re.sub(r"\bcreate-skill\b", "authoring-a-skill", d)
        if gated and not d.startswith(GATE_PREFIX):
            d = GATE_PREFIX + d
        out["description"] = d
    else:
        out["description"] = GATE_PREFIX + dirname if gated else dirname
    if target == "prime-agent":
        # Gated skills carry the flag; entry skills keep upstream's choice
        # (poteto-mode and poteto-help stay true, setup-pstack stays false).
        if gated or fm.get("disable-model-invocation"):
            out["disable-model-invocation"] = True
    else:
        meta = {}
        for k in ("mode", "icon", "color", "reminder", "disable-model-invocation"):
            if k in fm:
                meta[k.replace("-", "_")] = (
                    str(fm[k]).lower() if isinstance(fm[k], bool) else str(fm[k])
                )
        if gated:
            meta["disable_model_invocation"] = "true"
        if meta:
            out["metadata"] = meta
        out["license"] = "MIT"
        out["compatibility"] = "opencode"
    return out


# ---------------------------------------------------------------------------
# poteto-mode SKILL.md targeted rewrites
# ---------------------------------------------------------------------------

# Local policy: one persistent subagent per workstream, replacing upstream's
# "fresh subagents by default".
PERSISTENT_SUBAGENTS_OC = """**Persistent subagents per workstream.** Keep one subagent conversation per ongoing workstream, such as a PR, a feature, or an investigation thread. Record each one's role, workstream, and returned session ID in your todo list. For related follow-up work (a fix round, a follow-up question, a retry after an interruption, the next round of the same reviewer), continue that exact subagent by passing its session ID. Send it the new request plus every directive and change since its last turn. It has not seen the parent conversation. Start a fresh subagent only for unrelated work, for an independent review or second opinion (a clean context is the point), or when the session cannot be resumed. If a resume fails, say so in the reply instead of silently starting fresh. A stop or hold order to a running agent is not a new workstream. Never trust a "done" summary. Review the diff or artifact yourself."""

PERSISTENT_SUBAGENTS_PRIME = """**Persistent subagents per workstream.** Keep one child per ongoing workstream, such as a PR, a feature, or an investigation thread. Give it a stable `name=` and record its role and workstream in your todo list. For related follow-up work (a fix round, a follow-up question, a retry after an interruption, the next round of the same reviewer), message that exact child with `agent_message.send`, or use a resident session (`rlm.create_session`) for work that spans many turns. Send it the new request plus every directive and change since its last turn. It has not seen the parent conversation. Spawn a fresh child only for unrelated work, for an independent review or second opinion (a clean context is the point), or when the child cannot be reached. If messaging fails, say so in the reply instead of silently starting fresh. A stop or hold order to a running child is not a new workstream. Never trust a "done" summary. Review the diff or artifact yourself."""

AGENT_DESC_PERSISTENT = (
    r"Spawn a fresh `poteto-agent` for each new task, and resume one only in the\s+"
    r"strict cases that poteto-mode's Subagents section names\.",
    "Keep one `poteto-agent` per workstream and continue it for related follow-ups, "
    "as poteto-mode's Subagents section describes.",
)

# Upstream playbook lines that prescribe respawning where the persistent
# policy continues the same subagent. Exact-text regexes; a miss is reported
# by check_persistent_rewrites() so an upstream rewording cannot slip by.
PERSISTENT_PLAYBOOK_REWRITES = [
    (
        r"Unless the operator names a path, write the file under the agent store's `docs/`\.",
        "Unless the operator names a path, write the file under `${TMPDIR:-/tmp}/pstack/<repo>-<task-slug>/`, never inside the project.",
    ),
    (
        r"Keep it at `decisions\.tsv` in the work dir, or `\.audit/<task-slug>\.tsv` when several efforts run at once, and leave it out of git\.",
        "Keep it at `${TMPDIR:-/tmp}/pstack/<repo>-<task-slug>/decisions.tsv`, outside the project, and say its path in the reply.",
    ),
    (
        r"Commit it only when the work is ambitious enough that a reviewer needs the trail to trust the result\.",
        "Copy it into the project or commit it only when the user asks.",
    ),
    (
        r"figure-it-out's work is usually ambitious enough to commit the trail so the reviewer can read it in the PR\.",
        "Keep the trail outside the project and offer to commit it if the reviewer needs it.",
    ),
    (
        r"Spawn a fresh owner rather than chaining interrupts\.",
        "Keep the same owner across rounds and send it every directive since its last turn, "
        "rather than chaining interrupts.",
    ),
    (
        r"Never resume-chain a brief\. Respawn fresh with consolidated scope\.",
        "Continue the same worker for a related round, sending it the consolidated scope "
        "(the brief plus every directive since its last turn). Respawn fresh only when it cannot be resumed.",
    ),
    (
        r"`verifier-blocked` is not a pass\. Respawn when the environment heals\.",
        "`verifier-blocked` is not a pass. Continue the same verifier when the environment heals.",
    ),
    (
        r"and respawn that worker once\.",
        "and send that worker one correction request in its existing session.",
    ),
]


def check_persistent_rewrites(src: Path) -> None:
    """Warn when an upstream line targeted by PERSISTENT_PLAYBOOK_REWRITES no longer exists."""
    corpus = "\n".join(
        p.read_text(encoding="utf-8") for p in (src / "skills").rglob("*.md")
    )
    for pat, _ in PERSISTENT_PLAYBOOK_REWRITES:
        if not re.search(pat, corpus):
            warn(f"persistent-subagent rewrite matched nothing: {pat}")


# Inserted before "## Non-negotiables": the visible per-turn contract. The
# triggers below it say what to load; this says when, and makes skipping show.
EVERY_TURN = """## Every turn

Do these on the first message and on every new user message in the session, before other work. Follow-up turns are where this mode decays.

1. **Name the playbook.** Write `playbook: <name>` at the top of your reply, then copy its numbered steps into the todo list verbatim. With no todo tool, put the steps as a checklist in your first message. A step you skip stays listed as `skip: <reason>`.
2. **Load each routed skill in this turn.** When a step or trigger names a skill (`how`, `why`, `architect`, `interrogate`, `swarm`, `figure-it-out`), call the skill tool for it now. Do not substitute a generic subagent or your memory of the skill. Skip any skill or principle already loaded earlier in this session, since its text is still in your context. The skill sets the subagent mix and the output shape, so skipping it skips both.
3. **Load the principles you lean on.** Pick the one to three principles from the index below that fit the task and load their leaf skills now, before the work they shape. Name each in the reply with the choice it changed. Citing none because you read none is a skipped step, not compliance.
4. **Close the loop.** Before the final reply, every todo is done or carries `skip: <reason>`. Load `unslop` and draft the reply against it.

5. **Keep working files out of the project.** Never create a playbook, plan, decision log, ledger, or checkpoint file inside the user's repo or working directory unless the user names that path or asks you to commit it. Keep playbook steps in the todo list or the chat. Write any other working file under `${TMPDIR:-/tmp}/pstack/<repo>-<task-slug>/` and give its path in the reply. This overrides any skill or playbook that says to write, keep, or commit such a file in the project. Skills whose product is a project file, such as `create-verification-skill`, still write it, because the user asked for it.

The only exemption is a one-line factual answer or a trivial mechanical edit. Write `playbook: none (trivial)` and answer directly.

"""

POTETO_SUBAGENTS_OC = (
    """**Spawn the `poteto-agent` subagent for any delegate you launch inside a playbook step** (code-writing delegates, ad-hoc helpers): it is installed as a `mode: subagent` agent, so a `task` call with subagent type `poteto-agent` gets the full style. `/poteto-mode` and `poteto-agent` route through the same body of rules. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own subagent mix for diverse-model review; respect what the skill prescribes, don't override to `poteto-agent`.

**Defaults for every `task` call.** Prefer parallel spawns: launch independent delegates in one message and keep the main thread on summaries. Pass file pointers, not inlined context. Omit `model` unless a role line in `pstack-models.md` sets one — the default for every role is `inherit-parent`, so delegates run on the parent model out of the box with zero hard-coded slugs. Define per-role models once via `/setup-pstack` (it detects your `opencode models` slugs and writes them into `pstack-models.md`); a role with no line keeps `inherit-parent`, and `auto` is an alias for it. Code delegates tier by difficulty only when roles are configured: the hardest changes go to your strongest judgment model, trivial edits to the fast model. Per-role lines override the model choices in the routed skills (`how`, `why`, `arena`, `swarm`, `architect`, `interrogate`, `reflect`). Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`.

You own every subagent's work. Review the diff and write your own summary, don't pass through what it said. A second opinion is the same prompt against a different model. Agreement is high-signal.

"""
    + PERSISTENT_SUBAGENTS_OC
)

POTETO_SUBAGENTS_PRIME = (
    """**Spawn every delegate inside a playbook step as an `rlm.spawn` child whose prompt opens with the poteto-agent spec**: `You are operating as poteto-mode's full agent style. Read the poteto-mode skill's SKILL.md in full before doing any work, including its inline Principles index. Navigate to a leaf principle-* skill whenever you apply that principle.` The same paragraph is kept at `agents/poteto-agent.md` in this package. Routed workflow skills (`how`, `why`, `interrogate`, `reflect`, `swarm`) set their own model mix for diverse-model review; respect what the skill prescribes.

**Defaults for every `rlm.spawn` call.** Children run concurrently once spawned — fan out independent delegates, keep the main thread on summaries, and `rlm.collect` the results. Pass file pointers, not inlined context. Omit `model` unless a role line in `~/.prime/agent/pstack-models.md` sets one — the default for every role is `inherit-parent`, so children run on the parent model out of the box with zero hard-coded slugs. Define per-role models once via `/skill:setup-pstack` (it calls `rlm.find_models()` and writes them into `pstack-models.md`); `auto` is an alias for `inherit-parent`, and `thinking` defaults to the parent level. Code delegates tier by difficulty only when roles are configured: the hardest changes go to your strongest judgment model (higher `thinking`); trivial edits to the fast model. Per-role lines override the model choices in the routed skills. Each code playbook's configured model comes from its line (`feature, refactoring`, `bug-fix`, `perf-issue`, or `hillclimb`), and the hardest changes read `hardest tasks`. Prose and judgment read `judgment and prose`. For a standing fan-out with joins, gates, or per-item children, prefer `rlm.factory` machines over ad-hoc spawn loops; enable it once with `/factory on`.

You own every subagent's work. Review the diff and write your own summary, don't pass through what it said. A second opinion is the same prompt against a different model. Agreement is high-signal.

"""
    + PERSISTENT_SUBAGENTS_PRIME
)

# Additional poteto-mode body tweaks per target (list of (old, {oc, prime})).
POTETO_EDITS = [
    (
        "Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).",
        {
            "opencode": "Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the `authoring-a-skill` playbook's conventions for SKILL.md files.",
            "prime-agent": "Any prose surface → the **unslop** skill. Your reply is a prose surface. Write it per **Writing the reply**. Agent-facing prose also follows the `skill-creator` skill (prime-agent's built-in for authoring SKILL.md files).",
        },
    ),
    (
        "- Bug fix. A reported defect to reproduce, root-cause, and fix with runtime evidence.",
        None,
    ),  # unchanged, anchor only
]

HARNESS_NOTE = {
    "opencode": (
        "## Harness notes (opencode port)\n\n"
        '- Skills load on demand through the `skill` tool; `/poteto-mode` and `/setup-pstack` are real commands installed by this port. Other `/name` mentions in the ported text mean "load the `name` skill".\n'
        "- Subagents run through the harness's subagent tool (`task` in stock opencode; some hosts name it `subagent`). Its returned session ID is what you pass to continue a persistent subagent. `poteto-agent` and `comment-sicko` are installed as `mode: subagent` agents, and `poteto` is installed as a `mode: primary` agent you can Tab to.\n"
        "- The model role table lives at `~/.config/opencode/pstack-models.md` (registered in `opencode.json` `instructions`); `/setup-pstack` writes it.\n"
        "- Models are slug-free by default: every role is `inherit-parent` (omit `model`) until you assign `provider/model` slugs via `/setup-pstack`. Skills never hard-code a model slug.\n"
        "- `Bugbot` means the forge's automated PR reviewer — whatever bot posts review comments on your PRs (Bugbot on Cursor; Copilot review, Devin Review, or a CI bot elsewhere). Triage it skeptically per `references/bugbot-triage.md`.\n"
        "- `Origin` is optional: where a playbook says `origin pr ...`, it applies only when the `origin` CLI exists and resolves the repo; otherwise stay on `gh`.\n"
        "- Where text references a periodic wake (`/loop` upstream), use a background task or watcher that re-arms; `scripts/watch-pr` covers GitHub PR wakes.\n"
        "- The `deslop`/`control-cli`/`control-ui` skills live in a different upstream plugin. Where they're named, use them if your setup ships them, else do the equivalent pass by hand.\n"
        "- The verification-skill convention (`.opencode/skills/verify-<app>/`) works as-is: project-local skills are discovered automatically.\n"
        "- Gating: every skill except `/poteto-mode`, `/setup-pstack`, and `/poteto-help` carries `disable_model_invocation` and a description gate — the harness must not trigger pstack skills on its own. They load only when the task runs under `/poteto-mode`, which routes to them explicitly per playbook.\n"
    ),
    "prime-agent": (
        "## Harness notes (prime-agent port)\n\n"
        "- Skills load via the skill inventory; explicit invocation is `/skill:<name>` (for example `/skill:poteto-mode`). `disable-model-invocation` skills stay hidden until invoked.\n"
        "- Subagents are spawned children: `await rlm.spawn(prompt, name=..., model='provider/model', thinking='low|medium|high')`, collected with `rlm.collect`, and inspected with `agent_observe`/`agent_message`. For shaped fan-out (joins, bounded loops, per-item children) enable and use `rlm.factory` (`/factory on`).\n"
        "- The model role table lives in `~/.prime/agent/pstack-models.md` (a standalone file, like opencode's); `/skill:setup-pstack` writes it and keeps a pointer block in `~/.prime/agent/AGENTS.md`. Delete the pointer block (and the file) to stop applying pstack models. `/skill:recall`-style transcripts live under `~/.prime/agent/sessions/`.\n"
        "- Models are slug-free by default: every role is `inherit-parent` (omit `model`) until you assign `provider/model` slugs via `/skill:setup-pstack`. Skills never hard-code a model slug.\n"
        "- `Bugbot` means the forge's automated PR reviewer — whatever bot posts review comments on your PRs (Bugbot on Cursor; Copilot review, Devin Review, or a CI bot elsewhere). Triage it skeptically per `references/bugbot-triage.md`.\n"
        "- `Origin` is optional: where a playbook says `origin pr ...`, it applies only when the `origin` CLI exists and resolves the repo; otherwise stay on `gh`.\n"
        "- Where upstream text says `/loop`, use a scheduled wake: `rlm-heartbeat`, a goal, or a daemon schedule.\n"
        "- `skill-creator` replaces upstream's `create-skill`; `deslop`/`control-cli`/`control-ui` are upstream-plugin skills — use them if installed, else do the equivalent pass by hand.\n"
        "- Long-running program playbooks (orchestrate, autopilot-*) map best onto the factory plus resident sessions (`rlm.create_session`).\n"
        "- Gating: every skill except `poteto-mode`, `setup-pstack`, and `poteto-help` carries `disable-model-invocation` and a description gate — skills stay hidden until invoked. They load only when the task runs under `/skill:poteto-mode`, which routes to them explicitly per playbook.\n"
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

Every role defaults to `inherit-parent` (omit `model`, run on the parent model), so the file works with zero hard-coded slugs. If `~/.config/opencode/pstack-models.md` already exists, read it and treat its `# budget` line and its role values as the current choices. Otherwise start from all-`inherit-parent`. A line whose role is not in step 5, such as `how critics`, is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Prefer a structured choice prompt over free text. Offer these four options with these exact labels, and name the current budget when the file records one.

- `unlimited — strongest slug per role`
- `large — strong slugs, fast tier where a role tolerates it`
- `medium — mid-tier slugs`
- `small — cheapest slugs`

**(b) Apply it.** Budget moves between model tiers instead of effort tokens: keep each role's configured family but swap the member (for example fast-tier → mid-tier → strongest-tier as budget rises). `inherit-parent` and `auto` do not change. If the result is not a detected slug, use the same family's detected slug nearest the target tier, else mark the role as needing a choice.

**(c) Show the roles and confirm.** Show every role with its model, marking any real slug not in the detected set as needing a choice. Also list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent` and `auto` (both mean: this role runs on the parent agent's model — opencode subagents inherit the invoking agent's model when `model` is omitted). For panel roles (arena runners, architect runners, interrogate reviewers) the value is a list, and one subagent runs per entry, alias entries included, so the list length sets the count. `arena cross-judge pool` is also a list, but Arena selects one value from it whose model family differs from the parent's when possible. `swarm workers` is the default model for every worker unless a race or comparison assigns another model per arm.

### 4. Validate

Every real slug written must be in the detected set. `inherit-parent` and `auto` always pass. If a chosen real slug is not available, stop and ask again.

### 5. Write the file

Write `~/.config/opencode/pstack-models.md` with a `# budget` line naming the chosen label and one line per role, using the same labels poteto-mode uses. Overwrite the whole file so re-runs stay idempotent. Then ensure `~/.config/opencode/opencode.json` contains `\"{MODELS_FILE["opencode"].strip("`")}\"`-equivalent entry in its `instructions` array (add the file's absolute path if missing; leave any existing entries intact). Shape:

```
# pstack model configuration. One line per role. Delete a line to fall back to the skill default (inherit-parent).
# `inherit-parent` or `auto` as a value: the role runs on the parent model (omit the task `model`). Alias entries in a panel list still count toward its fan-out.
# budget: unlimited
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

### 6. Confirm

Tell the user the file was written and that it applies to new sessions. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with the `create-verification-skill` skill." On yes, load and run `create-verification-skill`. On no, move on without pushing.
"""

SETUP_PRIME = f"""# Setup pstack (prime-agent)

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
                'transcripts="${PSTACK_TRANSCRIPTS_DIR:-$HOME/.cursor/projects/$slug/agent-transcripts}"',
            )
            text = text.replace(
                "# Transcripts dir: ~/.cursor/projects/<slugified-repo-path>/agent-transcripts.",
                "# Transcripts dir defaults to Cursor's layout. Point PSTACK_TRANSCRIPTS_DIR at the\n"
                "# harness's session store instead (e.g. ~/.prime/agent/sessions for prime-agent,\n"
                "# ~/.local/share/opencode/storage for opencode).",
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
    repl = POTETO_SUBAGENTS_PRIME if target == "prime-agent" else POTETO_SUBAGENTS_OC
    # Match "## Subagents" up to the next "## " heading so an upstream rename of
    # the following section never aborts the build.
    m = re.search(r"^## Subagents\b.*?(?=^## )", body, re.S | re.M)
    if m:
        body = body[: m.start()] + "## Subagents\n\n" + repl + "\n\n" + body[m.end() :]
    else:
        warn("'## Subagents' heading not found in poteto-mode; appending")
        body = body.rstrip() + "\n\n## Subagents\n\n" + repl + "\n"
    if "## Non-negotiables" in body:
        every_turn = EVERY_TURN
        if target == "prime-agent":
            every_turn = every_turn.replace(
                "call the skill tool for it now",
                "load it now with `/skill:<name>`, or read its installed `SKILL.md` when you cannot invoke it",
            )
        body = body.replace("## Non-negotiables", every_turn + "## Non-negotiables", 1)
    else:
        warn("'## Non-negotiables' not found in poteto-mode; prepending Every turn")
        body = EVERY_TURN + body
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

You are in poteto mode. At the start of every task, load the `poteto-mode` skill in full — including its inline Principles index — then follow its "Every turn" section: name the playbook and copy its steps into your task list verbatim, load the routed skills and principles it names, and close every step before the reply. Repeat this on every new user message, not only the first. All poteto-mode rules apply for the whole session: deliberate subagents, unslopped prose, simple code, verified work.
""",
}

# Skills that also ship as real invocation surfaces: commands for opencode,
# prompts for prime-agent. (poteto-help is upstream's other word-loaded skill.)
COMMAND_SKILLS = {"poteto-mode", "setup-pstack", "poteto-help"}

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
    "poteto-help.md": """---
description: Ask which pstack skill/playbook/principle fits a task, or how to install and use pstack
---
Load the `poteto-help` skill and answer the user's question with it. $ARGUMENTS
""",
}

PRIME_PROMPTS = {
    "poteto.md": """Load and apply the `poteto-mode` skill to this request (`/skill:poteto-mode` is the explicit form): $1""",
    "setup-pstack.md": """Run the `setup-pstack` skill: $1""",
    "poteto-help.md": """Load the `poteto-help` skill (`/skill:poteto-help` is the explicit form) and answer with it: $1""",
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
        desc = str(fm.get("description", f.stem)).strip().strip('"').strip("'")
        desc = transform_markdown(desc, "opencode", f.name)
        new_fm = {"description": desc, "mode": "subagent"}
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
        desc = str(fm.get("description", f.stem)).strip().strip('"').strip("'")
        desc = transform_markdown(desc, "prime-agent", f.name)
        new_fm = {"name": f.stem, "description": desc}
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

Both harnesses at once (or pick one):

```sh
./install.sh             # install into prime-agent and opencode
./install.sh prime       # ~/.prime/agent only
./install.sh opencode    # ~/.config/opencode only

./uninstall.sh           # same targeting; removes only the files this repo
                         # ships plus the /setup-pstack artifacts, leaving
                         # anything you added yourself untouched
```

Destinations default to `~/.prime/agent` and `~/.config/opencode`; override
with `PRIME_DST` / `OPENCODE_DST`. Per-harness scripts are below — the
wrappers just call them.

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
`/skill:setup-pstack` writes the per-role model table to
`~/.prime/agent/pstack-models.md` and keeps a pointer block in
`~/.prime/agent/AGENTS.md` — delete the pointer block (and the file) when you
do not want pstack model config to apply.

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


# ---------------------------------------------------------------------------
# Upstream fetch / stamping / overrides / drift report
# ---------------------------------------------------------------------------


def resolve_ref(ref: str) -> str:
    """Resolve a ref/branch/tag to a full commit sha via `git ls-remote`.

    Avoids the api.github.com rate limit; a 40-hex ref is used as-is."""
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        return ref
    url = f"https://github.com/{UPSTREAM_REPO}.git"
    out = subprocess.run(
        ["git", "ls-remote", url, ref], capture_output=True, text=True, check=True
    ).stdout
    # Prefer heads/<ref>, then tags/<ref>, then any match.
    candidates = []
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        candidates.append((sha.strip(), name.strip()))
    for kind in (f"refs/heads/{ref}", f"refs/tags/{ref}"):
        for sha, name in candidates:
            if name == kind:
                return sha
    if candidates:
        return candidates[0][0]
    sys.exit(f"could not resolve upstream ref {ref!r} via git ls-remote")


def fetch_upstream(ref: str, cache: Path) -> tuple[Path, str]:
    """Download the pstack subtree at `ref` into `cache`; return (src_dir, sha)."""
    sha = resolve_ref(ref)
    url = f"https://github.com/{UPSTREAM_REPO}/archive/{sha}.tar.gz"
    print(f"fetching {UPSTREAM_REPO}@{sha[:12]} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "pstack-port"})
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    root_prefix = f"plugins-{sha}/{UPSTREAM_SUBDIR}/"
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        members = [
            m for m in tf.getmembers() if m.name.startswith(root_prefix) and m.isfile()
        ]
        for m in members:
            m.name = m.name[len(root_prefix) :]
            tf.extract(m, cache, filter="data")
    return cache, sha


def write_stamp(dst: Path, src_desc: str) -> None:
    write(dst / "UPSTREAM", src_desc + "\n")


def apply_overrides(dst_root: Path, target: str) -> int:
    """Copy overrides/<target>/ files verbatim over the generated output."""
    odir = dst_root / "overrides" / target
    if not odir.is_dir():
        return 0
    n = 0
    for f in sorted(odir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(odir)
            out = dst_root / target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            n += 1
    return n


def _hash_tree(root: Path) -> dict[str, str]:
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()[
                :16
            ]
    return out


def drift_report(new_root: Path, old_root: Path) -> str:
    """Human-readable summary of what changed between generated trees."""
    lines = []
    for target in ("opencode", "prime-agent", "extras"):
        new, old = _hash_tree(new_root / target), _hash_tree(old_root / target)
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        changed = sorted(p for p in set(new) & set(old) if new[p] != old[p])
        if not (added or removed or changed):
            continue
        lines.append(f"## {target}")
        for label, group in (
            ("added", added),
            ("removed", removed),
            ("changed", changed),
        ):
            if group:
                lines.append(f"  {label} ({len(group)}):")
                lines += [f"    {p}" for p in group]
    return "\n".join(lines) or "no changes"


def build_all(src: Path, dst: Path) -> None:
    global ACTIVE_SKILLS
    ACTIVE_SKILLS = {d.name for d in (src / "skills").iterdir() if d.is_dir()}
    check_persistent_rewrites(src)
    build_opencode(src, dst / "opencode")
    build_prime(src, dst / "prime-agent")
    build_extras(src, dst / "extras")
    write(dst / "README.md", README)
    write(dst / "ADAPTATIONS.md", ADAPTATIONS)
    for target in ("opencode", "prime-agent"):
        n = apply_overrides(dst, target)
        if n:
            print(f"applied {n} override(s) to {target}/")


def fail_if_strict(strict: bool) -> None:
    """Exit 1 when --strict is set and the build printed warnings."""
    if strict and WARNINGS:
        sys.exit(
            f"{len(WARNINGS)} warning(s); a local rewrite no longer matches upstream"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", help="local pstack checkout (default: fetched cache)")
    ap.add_argument(
        "--dst",
        default=str(Path(__file__).resolve().parent),
        help="destination repo root (default: directory containing port.py)",
    )
    ap.add_argument(
        "--fetch",
        action="store_true",
        help="download upstream pstack at --ref into .upstream/ and build from it",
    )
    ap.add_argument(
        "--ref", default="main", help="upstream ref/tag/sha (default: main)"
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any warning was printed (an upstream rewording broke a local rewrite)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="regenerate to a temp dir and print a drift report (no writes)",
    )
    args = ap.parse_args()
    dst = Path(args.dst)
    cache = dst / ".upstream" / UPSTREAM_SUBDIR

    src_desc = None
    if args.src:
        src = Path(args.src)
        src_desc = f"local checkout: {src}"
    elif args.fetch:
        src, sha = fetch_upstream(args.ref, cache)
        (cache.parent / "SHA").write_text(sha + "\n")
        src_desc = f"{UPSTREAM_REPO}@{sha} ({UPSTREAM_SUBDIR}/ subtree)"
    elif cache.is_dir():
        src = cache
        sha_file = cache.parent / "SHA"
        sha = sha_file.read_text().strip() if sha_file.exists() else "unknown"
        src_desc = f"{UPSTREAM_REPO}@{sha} ({UPSTREAM_SUBDIR}/ subtree, cached)"
    else:
        sys.exit(
            "no source: pass --fetch to download upstream or --src DIR for a local checkout"
        )

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            build_all(src, Path(tmp))
            print(drift_report(Path(tmp), dst))
        fail_if_strict(args.strict)
        return

    build_all(src, dst)
    if src_desc:
        write_stamp(dst, src_desc)
    print("wrote", dst)
    fail_if_strict(args.strict)


if __name__ == "__main__":
    main()
