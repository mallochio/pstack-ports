# overrides/

Files here are applied to generated output at the end of every `port.py` run.
Mirror the output path under the target dir for a whole-file replacement:

    overrides/opencode/skills/how/SKILL.md   ->  opencode/skills/how/SKILL.md
    overrides/prime-agent/prompts/poteto.md  ->  prime-agent/prompts/poteto.md

For a smaller edit, add a unified diff with `.patch` appended to the output
path. Generate it from the repo root after saving the generated and edited
versions:

    diff -u --label a/skills/how/SKILL.md --label b/skills/how/SKILL.md \
      /tmp/SKILL.generated.md /tmp/SKILL.edited.md \
      > overrides/opencode/skills/how/SKILL.md.patch

`port.py` applies the patch after whole-file overrides. If upstream changes the
patched context, the build stops and names the patch and target file.

`overrides/BASES.sha256` records the generated version replaced by each
whole-file override. A warning means that upstream changed that version. Review
the override before you update its recorded base.

Generated trees (`opencode/`, `prime-agent/`, `extras/`) are rebuilt wholesale.
Do not edit them in place. `GENERATED.sha256` detects edits before a build.
Move the change into an override or rebuild with `--force` after review.
