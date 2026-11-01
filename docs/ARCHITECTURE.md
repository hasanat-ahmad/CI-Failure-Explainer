# Architecture

This document is for maintainers and for engineers evaluating the skill for team or production use. User-facing docs are in the [README](../README.md).

## Design goals

1. **Context efficiency:** the agent sees a ≤150-line digest, never a 100k-line log.
2. **Safety:** secrets are removed before any other processing. Log content is treated as untrusted.
3. **Executable expertise:** the human-readable reference library is also the machine-readable pattern database.
4. **Method and consistency:** a fixed diagnostic method and report format.
5. **Zero-dependency portability:** Python 3.9+ standard library only. Every script runs standalone or as an imported module.

## Components

```text
ci_doctor.py ─────────────┬─ fetch_failed_logs.py   gh → run metadata, failed-step log, history, last green
  (orchestrator,          ├─ clean_logs.py          redact → normalise → collapse → classify → select → render
   failure-tolerant)      ├─ detect_stack.py        repo files + log signals → stacks, toolchain pins
                          ├─ match_patterns.py      references/*.md → regex entries → ranked matches
                          └─ workflow_steps.py      .github/workflows → failing step, pins, secrets, repro.sh
```

`ci_doctor` treats **fetch** and **digest** as core phases (failure ⇒ clear error + exit code). **Stack detection, pattern matching and workflow mapping** are optional phases: an exception becomes a `Warning:` line in the brief and the remaining phases still run.

## The digest algorithm (`clean_logs.py`)

1. **Redact** each line's content (stateful for multi-line private keys), strip ANSI and `\r` progress frames, then redact again (ANSI codes can split tokens). Lines are truncated to 2,000 chars **after** redaction.
2. **Parse** `gh --log-failed` prefixes (`job\tstep\tTIMESTAMP`) and full-job logs (steps inferred from `##[group]Run …`). GitHub markers (`##[error]`, `::error file=…::`) become `[error] …`, and `::add-mask::` lines are dropped.
3. **Collapse** consecutive near-duplicates (digits normalised) into `(×N similar lines)` and fold stack-frame runs longer than 8 lines (keep 3 + 3).
4. **Classify** each line as *specific* (root-cause grade, e.g. `error TS2322`, `E   ModuleNotFoundError`, Gradle's `What went wrong` chain), *generic* (`error`, `FAILED`), *summary* (exit codes, `Tests: 1 failed`) or *noise* (downloads, progress, passing tests, cache chatter, BuildKit-prefixed npm warnings).
5. **Pick anchors.** The root cause is the first specific line in a failed step (following Gradle's nested `>` chain to the deepest reason). The final symptom is the last summary line.
6. **Select windows greedily under the line budget**, in priority order: root cause (shrinking context 15→0), exit-code line, step tail (20→3), final symptom, then other distinct anchors. Each candidate is accepted only if the *rendered* markdown still fits, so the budget is exact. The search stops after 200 extra anchors or 8 consecutive misses.
7. **Render** markdown (default) or JSON. The header carries counts, root cause, exit code and a stable **fingerprint** (sha256 of the normalised root-cause line) for deduplicating recurring failures.

## Pattern matching (`match_patterns.py`)

Entries are parsed from `### ID · Title` headings with `**Signature/Category/Cause/Fix/Verify**` fields. Each backtick span in *Signature* is compiled as a Python regex and matched line by line. Score:

- each matching signature: `10 × specificity`, where specificity ∈ [0.4, 1.2] grows with the pattern's literal (non-meta) length, so precise explanations outrank symptom-level entries;
- signature matches the root-cause line: `+ 20 × specificity + 5`;
- entry's stack detected: `+5` (`general`/`flaky`: `+2`).

Confidence is *high* (root-cause hit or ≥2 signatures), *medium* (one signature) or *low*. `--validate` (run in CI) rejects missing fields, bad or empty-matching regexes, unknown categories and duplicate IDs.

## Output contract

All JSON outputs carry `schema_version` (currently `1`); additive changes keep the version, breaking changes bump it.

| Command | JSON top-level keys (stable) |
|---|---|
| `clean_logs.py --format json` | `schema_version, tool_version, source, raw_lines, digest_lines, secrets_redacted, redactions_by_type, injection_flags, failed_steps, root_cause, final_symptom, exit_code, fingerprint, steps` |
| `fetch_failed_logs.py` | `schema_version, tool_version, repo, run, failed_jobs, log_path, log_lines, last_success, same_sha_succeeded, history, other_failed_runs, notes, next` (on error: `schema_version, error, exit_code`) |
| `detect_stack.py` | `schema_version, root, stacks[{stack, confidence, evidence, reference}], references, toolchain` |
| `workflow_steps.py` | `schema_version, workflow_file, job_id, step_name, run, repro_script, toolchain, secrets_referenced, env_names, matrix, …` |
| `match_patterns.py --format json` | list of `{id, title, category, reference, line, score, confidence, on_root_cause, hits, cause, fix, verify}` |
| `ci_doctor.py --format json` | `schema_version, tool_version, bundle, bundle_is_temp, category_guess, flaky_signals, warnings, meta, stacks, workflow_step, matches, digest` |

### Exit codes (`fetch_failed_logs.py`, `ci_doctor.py`)

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Unexpected error (gh failure after retries, unreadable log) |
| 2 | Environment / usage (gh missing or unauthenticated, not a repo, bad arguments, missing file) |
| 3 | Run still in progress |
| 4 | No failed run found |
| 5 | Logs unavailable (expired / deleted) |

Transient `gh` failures (HTTP 5xx, resets, timeouts) are retried twice with exponential backoff (`CI_EXPLAINER_GH_RETRIES`, `CI_EXPLAINER_RETRY_DELAY`).

## Limits

| Limit | Value | Where |
|---|---|---|
| Digest lines | 150 (`--max-lines`, min 20) | `clean_logs.py` (`ci_doctor` default 120) |
| Input size | 256 MB, head 8 MB + tail kept | `MAX_INPUT_BYTES`, `HEAD_BYTES` |
| Per-line scan | 2,000 chars, cut after redaction | `MAX_SCAN_CHARS` |
| Displayed line | 400 chars | `MAX_LINE_CHARS` |
| `--slice` range | 400 lines; `--grep` 100 matches | `clean_logs.py` |
| Performance | 200k-line log with 4,000 distinct errors in ~5 s on a laptop | `tests/test_clean_logs.py::TestProductionLimits` |

## Testing strategy

- **Unit and integration tests** (`python -m unittest discover tests`): redaction (every rule, safe values, no leaks across markdown/JSON/slice/grep/bundle), digest budget and root-cause retention for every fixture, hostile inputs, YAML-subset parser, `gh` behaviour through a fake `gh` executable (success, retries, every error path), and end-to-end `ci_doctor` runs including phase degradation.
- **Eval gate** (`tests/run_eval.py`): the pipeline must diagnose ≥9/10 fixtures correctly (top pattern + category + root-cause line kept).
- **Fixture reproducibility:** fixtures are generated deterministically, and CI fails if regeneration changes them.
- **Matrix:** Python 3.9 / 3.12 / 3.13 on Ubuntu and macOS, plus ruff lint and an installer smoke test.
