<!--
Optional. Codex discovers skills natively from .agents/skills/ (repo) and ~/.agents/skills/ (user),
so installing the skill is enough. Paste the section below into your repository's AGENTS.md only
if you want Codex to always reach for the skill on CI questions, or if you use an older Codex
without skills support (then also copy skills/ci-failure-explainer/ to .agents/skills/).
-->

## CI failures

When asked why CI, a GitHub Actions workflow, a check or tests failed, or to fix a broken build:

1. Use the `ci-failure-explainer` skill (`.agents/skills/ci-failure-explainer/SKILL.md`). Start with
   `python3 .agents/skills/ci-failure-explainer/scripts/ci_doctor.py` (add `--pr N`, `--run URL` or `--log-file F`).
2. Never read raw CI logs directly. Use the redacted digest, or `clean_logs.py --slice/--grep`.
3. Report with `references/report-template.md`. Explain first, then fix minimally. Never weaken tests or checks
   to make CI pass. Never push, rerun workflows or change secrets without asking.
