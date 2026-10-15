---
name: principle-guard-the-context-window
description: 'Only use when poteto-mode is active for this task. Apply when context
  is filling up: large outputs, long files, repeated reads, fan-out planning. Route
  bulk to subagents; keep summaries in the main thread, not raw payloads.'
metadata:
  disable_model_invocation: 'true'
license: MIT
compatibility: opencode
---

# Guard the Context Window

**Why:** Context overflow degrades reasoning quality, creates compression artifacts, and halts progress.

**Pattern:**
- **Size phases and cap scope.** Limit files per phase, set turn budgets, account for mechanism costs.
