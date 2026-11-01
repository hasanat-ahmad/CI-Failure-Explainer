# Diagnostic method

Work through these steps in order. Skip a step only when its answer is already in the triage brief, and write down what each step found: those notes become the report's evidence.

## 0. Get the facts (one command)

```bash
python3 scripts/ci_doctor.py [--run <id|url> | --pr <n> | --branch <b> | --log-file <path>]
```

The brief contains the run metadata, history strip, redacted digest, detected stack, ranked known-pattern matches, the failing step's exact command, secrets it uses, toolchain pins and local versions. Only fall back to the individual scripts when you need a different view (`clean_logs.py --slice/--grep`, `workflow_steps.py --step ...`).

## 1. Classify the failure

Pick one primary category (it decides where to look):

| Category | Typical evidence | Look first at |
|---|---|---|
| **build** | compiler/type errors, bundler errors, `failed to solve` | the diff, toolchain versions |
| **test** | assertion output, `--- FAIL`, `●`, `FAILED tests/...` | the diff, then flakiness |
| **lint** | formatter/linter output, `--fatal-infos`, `--max-warnings 0` | tool version pins |
| **dependency** | lockfile, resolver, `ERESOLVE`, version solving, missing module | manifests + lockfiles |
| **config** | missing secret/env var, wrong runtime version, wrong path | workflow YAML, settings |
| **permissions** | 401/403, `Resource not accessible by integration` | `permissions:`, tokens, fork status |
| **infrastructure** | exit 137, disk full, timeout, runner lost, rate limit | runner limits, status pages |
| **flaky** | passes on rerun, timing/network/order/random | `flaky-tests.md` |

If a step's output shows two errors, the **first specific error** is usually the cause and the rest are knock-on effects. The digest marks it with `▶`.

## 2. Check flakiness signals

From the brief: `same_sha_succeeded`, attempt number, history strip, and whether the error is about timing, network, ordering or randomness. If at least two signals are present, read `flaky-tests.md` and **confirm** before claiming flakiness. Never conclude "flaky" from a single failure with no evidence.

## 3. Relate the failure to what changed

- Last green vs now red: `git diff --stat <last_success.head_sha>..<head_sha>`, then `git diff <a>..<b> -- <relevant paths>`.
- On a PR: `git diff origin/<base>...HEAD` (or `gh pr diff <n>`).
- Look for changed files that relate to the error: the file named in a compiler error, a test touching changed code, a manifest or lockfile change, a workflow or Dockerfile change, a renamed file (case!).
- **Nothing related changed?** Suspect the environment: a floating version (`latest`, `^`, unpinned action or tool), runner image update, expired secret, external service, or flakiness. Check the run date against upstream releases.

## 4. Match known patterns

Read the top entries in the brief's "Known-pattern matches". A **high** confidence match on the root-cause line is strong evidence, but still check it against the diff and the step's command. If nothing matches, read the stack reference's quick-orientation section and continue from first principles.

## 5. Reproduce locally (when possible)

- Use the exact command from the brief's "Failing step" section (or `repro.sh` in the bundle, after reviewing it). Read `.github/workflows/*.yml` for anything around it (env, working-directory, matrix, services).
- Close the CI/local gap: **OS** (Linux, case-sensitive, LF), **runtime versions** (compare CI pins with local versions in the brief), **env vars and secrets**, **clean state** (no caches, `rm -rf node_modules`, fresh venv, `flutter clean`), **lockfiles** (`npm ci`, `--locked`, `--frozen-lockfile`).
- For container builds, use `docker build --progress=plain --no-cache`.
- If you can't reproduce (missing secrets, macOS/Windows only, emulator), say so explicitly and lower your confidence.

## 6. Form the root-cause hypothesis

State it as **cause → mechanism → symptom**, backed by at least one log line and, when available, one diff line. Assign confidence:

- **high**: reproduced locally or the error names the exact cause, *and* it's consistent with the diff/history.
- **medium**: strong pattern match or clear log evidence, not reproduced.
- **low**: plausible but competing explanations remain. Give the top 2 hypotheses and the quickest check that tells them apart.

## 7. Fix minimally, verify, report

- Fix the **root cause**, in the smallest change. Prefer pinning or aligning versions over workarounds, and fixing code over loosening checks.
- **Never** make CI green by deleting, skipping or weakening tests, lint rules, type checks or `--fatal-*` flags, unless the user explicitly asks.
- Run the verify command from the matched entry (or the failing step's command) locally.
- Don't push, force-push, rerun workflows, or change repo settings/secrets without explicit permission.
- Write the report with `report-template.md`. If the fix is a pattern the library lacks, suggest adding an entry (see CONTRIBUTING.md).
