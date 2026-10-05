# overrides/

Files here are copied verbatim over the generated output at the end of every
`port.py` run. Mirror the output path under the target dir:

    overrides/opencode/skills/how/SKILL.md   ->  opencode/skills/how/SKILL.md
    overrides/prime-agent/prompts/poteto.md  ->  prime-agent/prompts/poteto.md

Use this for hand-patches that must survive an upstream re-port. Generated
trees (`opencode/`, `prime-agent/`, `extras/`) are rebuilt wholesale — do not
edit them in place.
