---
description: Diagnose why CI failed and propose the smallest verified fix (uses the ci-failure-explainer skill)
---

<!--
Optional Antigravity workflow. The skill itself is discovered automatically from .agents/skills/
(workspace) or ~/.gemini/config/skills/ (global). Copy this file to .agents/workflows/diagnose-ci.md
(or the legacy .agent/workflows/) to get a /diagnose-ci command.
-->

1. Locate the skill folder `ci-failure-explainer` (workspace `.agents/skills/` or global `~/.gemini/config/skills/`) and read its `SKILL.md`.
2. From the repository root run `python3 <skill-dir>/scripts/ci_doctor.py`. Pass `--pr <n>` or `--run <url>` if the user gave one. For a pasted log, save it to a file and use `--log-file`.
3. Read the triage brief. Do not open the raw log. Use `clean_logs.py --slice` / `--grep` if you need more lines.
4. Follow `references/diagnostic-method.md`: classify, check flakiness signals, relate to the diff, match known patterns, reproduce locally with the step's exact command.
5. Write the report using `references/report-template.md`.
6. Ask before applying the fix. After fixing, run the entry's **Verify** command. Never push, rerun workflows or change secrets without permission.
