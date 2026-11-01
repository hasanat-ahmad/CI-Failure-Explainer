# Changelog

## 1.0.1 (2026-11-01)

Fixes found by running the skill on a real repository (a Flutter + Firebase Functions monorepo) and on this repo's own first CI run. Each fix has a regression test.

- **Stack detection (monorepos):** no false JVM stack for nested Flutter apps (`app/android/`). Toolchain hints are now read per sub-project (`app/: dart sdk constraint`, `functions/: package.json engines`, …). Fixed `dependencies: flutter: sdk: flutter` being read as a Flutter version constraint.
- **Workflow step mapping:** steps now match the `Run <command>` titles that downloaded job logs use even for named steps. Previously 3 of 6 real failures were mapped to the wrong job.
- **Digest:** Dart/Flutter `[E]` test-failure headers are root-cause grade, so the pointer lands on the first failing test with its `Expected/Actual` diff. Passing-test progress lines and `pub get` outdated lists are noise.
- **New patterns:** FLUTTER-18 (asset/font declared in `pubspec.yaml` missing in CI) and GEN-20 (runtime version not available on the runner image, e.g. Python 3.9 on Ubuntu 26.04). 123 entries in total.
- **CI:** Python 3.9 runs on `ubuntu-24.04` (no build exists for 26.04, now `ubuntu-latest`), Python 3.14 added, and `actions/checkout@v6` / `actions/setup-python@v6` (Node 24) adopted.
- 80 unit and integration tests. Real-repo verification: 6/6 injected failures diagnosed (see `tests/EVAL.md`).

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
