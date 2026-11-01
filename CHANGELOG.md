# Changelog

## 1.0.0 (2026-11-01)

First release.

- `SKILL.md` with portable Agent Skills frontmatter for Claude Code, claude.ai, OpenAI Codex, Cursor and Google Antigravity.
- Scripts (Python 3.9+, standard library only):
  - `ci_doctor.py`: one-shot triage brief and bundle.
  - `fetch_failed_logs.py`: run/PR/branch resolution via `gh`, last green run, history strip, same-SHA signal.
  - `clean_logs.py`: secret redaction, noise removal, root-cause detection, budgeted digest, `--slice` / `--grep`.
  - `detect_stack.py`: stacks from files and logs, pinned toolchain hints.
  - `match_patterns.py`: executable known-error library with specificity-weighted ranking and `--validate`.
  - `workflow_steps.py`: failing step → exact command, setup-* pins, secrets, local version drift, `repro.sh`.
- 121 known-failure entries: Flutter, Node, Python, Docker, Go, Rust, JVM, GitHub Actions/general, flaky tests.
- Claude Code plugin and marketplace manifests, a multi-agent `install.sh`, and adapters for older agent versions.
- Automatic-mode workflow example for `anthropics/claude-code-action@v1` (read-only, one sticky PR comment).
- Production hardening: versioned JSON contract (`schema_version`), `--version` on every script, documented exit codes, input and per-line limits, bounded window search, `gh` retry with backoff, failure-tolerant `ci_doctor` phases, `--discard-raw`.
- 75 unit and integration tests, 11 synthetic fixtures, eval gate (10/10), ruff lint. Verified on Python 3.9 and 3.14.
- SECURITY.md (threat model), docs/ARCHITECTURE.md, Makefile, Dependabot, issue/PR templates, and a release workflow that attaches a checksummed skill zip.
