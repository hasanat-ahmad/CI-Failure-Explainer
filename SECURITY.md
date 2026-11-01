# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting (*Security → Report a vulnerability* on this repository). Include a minimal log or input that reproduces the problem. Please use fake credentials only, never real ones.

You can expect an acknowledgement within 5 working days. Fixes for confirmed issues ship as a patch release with a CHANGELOG entry.

**In scope:** a secret format that survives redaction, any path where raw (unredacted) log text reaches the digest, brief, bundle or `--slice`/`--grep` output, prompt-injection bypasses of the untrusted-content handling, unsafe behaviour of the example workflow, and the installer writing outside its documented directories.

## Threat model

CI Failure Explainer processes **untrusted, possibly secret-bearing text** (CI logs) and hands a summary to an **AI agent that can run commands**. The design assumes:

| Threat | Mitigation |
|---|---|
| Logs contain credentials (unmasked env dumps, URLs with tokens, private keys) | Redaction runs on every line **before** any parsing, classification, matching or output, with a second pass after ANSI stripping. Lines are truncated only *after* redaction. Outputs report counts, never values. Raw logs are written `0600` in a `0700` temp dir, and `--discard-raw` deletes them. |
| The agent reads the raw log anyway | `SKILL.md` forbids it. Drill-down (`--slice`, `--grep`) goes through the same redactor. |
| Prompt injection via log content (test names, output, commit messages) | Every digest carries an "untrusted data" banner. Instruction-like lines are flagged `[!injection?]`. The skill tells the agent never to act on log content. The example workflow gives Claude read-only tools. |
| Hostile input sizes (huge files, megabyte lines, thousands of errors) | Input capped at 256 MB (head + tail kept), per-line regex work capped at 2,000 chars, window search bounded (≤200 extra anchors, early stop when the budget is full). Covered by tests. |
| Outward-facing side effects | The skill requires explicit user permission before push, force-push, reruns, PR comments, settings or secret changes. The scripts only ever call read-only `gh` subcommands (`auth status`, `run list/view`, `pr view`). |
| Automatic mode runs on fork PRs with base-repo secrets (`workflow_run`) | The example workflow never checks out or executes PR code. It reads logs via the API, runs only this repo's pinned scripts, disallows `Edit`/`Write` for Claude, and uses least-privilege `permissions`. |
| Supply chain | Zero runtime dependencies (standard library only). Pin the skill to a release tag or commit SHA in automation. Dependabot keeps GitHub Actions updated. |

## Known limitations

Regex redaction recognises known token formats and obvious `key=value` assignments. It **can't** recognise an arbitrary secret printed on its own (e.g. a random password echoed bare). GitHub's own `***` masking of registered secrets remains the first line of defence, and this tool is a second layer. If a secret appears in CI output, rotate it.
