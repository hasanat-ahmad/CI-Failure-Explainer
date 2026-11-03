# General / GitHub Actions platform failures

These apply to every stack: secrets, runners, permissions, platform limits and cross-OS differences. Read this file in addition to the stack-specific one, and on its own when no stack is detected (Go, Rust, JVM, Ruby, .NET and PHP also have dedicated files).

Signatures are Python regexes (backtick-quoted, ` · ` separated) used by `scripts/match_patterns.py`. A backtick inside a log line is written as `.`.

### GEN-01 · Missing secret or action input (incl. fork PRs and Dependabot)
- **Signature:** `Input required and not supplied: \S+` · `secret\w* (?:is|are) (?:not set|empty|missing)` · `Credentials could not be loaded` · `Error: Missing (?:required )?(?:secret|token|credentials)`
- **Category:** config
- **Cause:** The workflow passes `${{ secrets.X }}`, which resolves to an **empty string** when the secret doesn't exist, has a different name, lives in an environment the job doesn't reference, or the run comes from a **fork PR** or a **Dependabot** PR (both get no repository secrets on `pull_request`).
- **Fix:** Check the exact name in Settings → Secrets and variables → Actions (and `environment:` for environment secrets). For forks/Dependabot, skip secret-dependent steps instead of failing:
  ```yaml
  if: ${{ github.event.pull_request.head.repo.fork == false && github.actor != 'dependabot[bot]' }}
  ```
  Dependabot runs read Dependabot secrets, not Actions secrets. Never "fix" this with `pull_request_target` plus a checkout of PR code.
- **Verify:** `gh secret list` (and `gh secret list --env <name>`)

### GEN-02 · Required environment variable not set
- **Signature:** `environment variable \S+ (?:is )?(?:not set|missing|required)` · `KeyError: '[A-Z][A-Z0-9_]{2,}'` · `: parameter (?:null or not set|not set)` · `\b[A-Z][A-Z0-9_]{2,}: unbound variable`
- **Category:** config
- **Cause:** Code reads an env var that exists locally (`.env`, shell profile) but isn't set in the workflow `env:`. `set -u` scripts fail on unset variables.
- **Fix:** Declare it at step, job or workflow level. Use `vars.*` for non-secret config and `secrets.*` for secrets. Give a sensible default where that's safe.
- **Verify:** Add a temporary `run: printenv | cut -d= -f1 | sort` step (names only)

### GEN-03 · Runner out of memory (SIGKILL / exit 137)
- **Signature:** `exit code 137` · `^\s*Killed\s*$` · `line \d+:\s+\d+ Killed\s+\S` · `signal SIGKILL` · `The runner has received a shutdown signal` · `was terminated by signal SIGKILL` · `lost communication with the server`
- **Category:** infrastructure
- **Cause:** The process used more RAM than the runner has (standard Linux runners: 16 GB on public repos, 7 GB on private), and the kernel OOM killer sent SIGKILL. Typical with parallel builds, large webpack/Next.js/Gradle/Rust builds, or many test workers.
- **Fix:** Reduce parallelism (`--maxWorkers=2`, `-j2`, `org.gradle.workers.max=2`), cap heaps (`NODE_OPTIONS=--max-old-space-size=4096`, `org.gradle.jvmargs=-Xmx3g`), split the job, or use a larger runner. Add swap as a stopgap.
- **Verify:** Run with `/usr/bin/time -v <cmd>` and check "Maximum resident set size"

### GEN-04 · Runner out of disk space
- **Signature:** `No space left on device` · `You are running out of disk space` · `System\.IO\.IOException: No space left`
- **Category:** infrastructure
- **Cause:** Hosted runners have limited free disk. Docker images, Android SDKs, caches and build outputs fill it.
- **Fix:** Free space first (`sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc` or `jlumbroso/free-disk-space`), prune Docker (`docker system prune -af`), and avoid caching huge directories.
- **Verify:** `df -h` before and after the heavy step

### GEN-05 · Job or step timeout
- **Signature:** `has exceeded the maximum execution time of \d+ minutes` · `The operation was canceled\.` · `##\[error\]The action has timed out\.`
- **Category:** infrastructure
- **Cause:** `timeout-minutes` (default 360 for jobs) was hit: a hang (waiting for input, open handles, deadlock, a server that never became ready) or a slow cold build.
- **Fix:** Find the last line printed before the hang (the digest tail) and fix that command, e.g. pass `--yes` / `CI=true` to stop prompts, add test-runner timeouts, or wait for services with `wait-on`. Set a realistic `timeout-minutes` so hangs fail fast.
- **Verify:** Reproduce the step locally with `timeout 600 <cmd>`

### GEN-06 · API rate limit (GitHub, registries)
- **Signature:** `API rate limit exceeded` · `secondary rate limit` · `HTTP 429` · `Too Many Requests` · `toomanyrequests`
- **Category:** infrastructure
- **Cause:** Unauthenticated GitHub API calls (60/hour per IP) from tools like `setup-*` downloading releases, or bursty API use by scripts. Docker Hub has its own pull limits.
- **Fix:** Authenticate calls: pass `token: ${{ github.token }}` to actions that accept it, set `GITHUB_TOKEN`/`GH_TOKEN` for scripts, cache downloads, and add backoff. Rerun as a short-term fix.
- **Verify:** `gh api rate_limit`

### GEN-07 · Network or registry outage
- **Signature:** `Could not resolve host` · `getaddrinfo (?:ENOTFOUND|EAI_AGAIN)` · `Temporary failure in name resolution` · `(?:502 Bad Gateway|503 Service Unavailable|504 Gateway Time-?out)` · `Connection timed out`
- **Category:** flaky
- **Cause:** Transient DNS or registry problems (npm, PyPI, Maven Central, Docker Hub, GitHub). Check status pages (githubstatus.com, status.npmjs.org).
- **Fix:** Rerun failed jobs. If it keeps happening, add retries to installs (`npm ci --fetch-retries=5`, `pip --retries 5`), cache dependencies, or use a registry mirror. Don't add retries to tests to hide real failures.
- **Verify:** `gh run rerun <id> --failed`

### GEN-08 · `GITHUB_TOKEN` permissions too narrow
- **Signature:** `Resource not accessible by integration` · `Permission to \S+ denied to \S+` · `HttpError: 403` · `The requested URL returned error: 403` · `refusing to allow a GitHub App to create or update workflow`
- **Category:** permissions
- **Cause:** New repos and orgs default `GITHUB_TOKEN` to read-only, and a `permissions:` block removes everything you don't list. Fork PRs always get read-only tokens. Changing files under `.github/workflows` needs the `workflows` scope (not available to `GITHUB_TOKEN`).
- **Fix:** Grant the minimum needed for that job:
  ```yaml
  permissions:
    contents: write        # push tags/commits
    pull-requests: write   # comment on PRs
  ```
  For fork PR comments, use a `workflow_run` workflow that runs in the base repo context.
- **Verify:** Rerun, then check the "GITHUB_TOKEN Permissions" group in "Set up job"

### GEN-09 · Deprecated actions or workflow commands
- **Signature:** `The .(?:set-output|save-state). command is deprecated` · `Node\.js (?:12|16) actions are deprecated` · `uses a deprecated version of .actions/(?:upload|download)-artifact: v[123].` · `This request has been automatically failed because it uses a deprecated version`
- **Category:** config
- **Cause:** GitHub retired old action runtimes and versions: `actions/upload-artifact@v3` and `download-artifact@v3` now fail hard, and old `actions/checkout`/`setup-*` ran on Node 12/16. `::set-output` was replaced by `$GITHUB_OUTPUT`.
- **Fix:** Bump to current majors (`actions/checkout@v4`, `actions/upload-artifact@v4`, ...) and use `echo "name=value" >> "$GITHUB_OUTPUT"`. Note that artifact v4 names must be unique per run. Let Dependabot keep actions updated (`package-ecosystem: github-actions`).
- **Verify:** `grep -rn "uses:" .github/workflows | grep -E "@v[123]\b"`

### GEN-10 · Windows runner shell differences
- **Signature:** `is not recognized as an internal or external command` · `The term '.+' is not recognized as (?:the )?name of a cmdlet` · `ParserError:` · `The system cannot find the path specified`
- **Category:** config
- **Cause:** Windows runners default to PowerShell (`pwsh`). Bash syntax (`export`, `&&` on old PowerShell, `$VAR`, `/` paths) breaks.
- **Fix:** Set `defaults: run: shell: bash` for cross-OS jobs, or write PowerShell explicitly for Windows steps. Use `path.join` / `os.path.join` in code.
- **Verify:** Run the step on `windows-latest` with `shell: bash`

### GEN-11 · CRLF line endings
- **Signature:** `/bin/(?:ba)?sh\^M: bad interpreter` · `\$'\\r': command not found` · `env: .\w+\\r.: No such file or directory` · `syntax error near unexpected token .\$'\\r'.`
- **Category:** config
- **Cause:** Scripts committed with Windows line endings fail on Linux runners.
- **Fix:** Add `.gitattributes` with `*.sh text eol=lf` (and `Dockerfile`, `gradlew`), then `git add --renormalize .` and commit.
- **Verify:** `file scripts/*.sh` (should not say CRLF)

### GEN-12 · Failure only in one matrix combination
- **Signature:** `The strategy configuration was canceled because "[^"]+" failed` · `##\[error\]The operation was canceled\.`
- **Category:** config
- **Cause:** One matrix entry (an OS, runtime version or dependency set) fails and `fail-fast: true` (the default) cancels the rest, so cancelled jobs look like failures.
- **Fix:** Find the first job that actually failed (not cancelled) and diagnose that one. Its matrix values point at the difference (old Node, Windows paths, Python 3.13 wheels). Use `fail-fast: false` to see every result.
- **Verify:** `gh run view <id> --json jobs -q '.jobs[] | [.name,.conclusion] | @tsv'`

### GEN-13 · Script not executable
- **Signature:** `\S+\.sh: Permission denied` · `\./gradlew: Permission denied` · `EACCES: permission denied, (?:spawn|open|mkdir)`
- **Category:** config
- **Cause:** The executable bit isn't set in git (common when committed from Windows), or a step writes into a root-owned directory.
- **Fix:** `git update-index --chmod=+x path/to/script.sh` (or `gradlew`), then commit. Or call `bash script.sh`.
- **Verify:** `git ls-files -s path/to/script.sh` (mode should be 100755)

### GEN-14 · Shallow clone: missing history or tags
- **Signature:** `fatal: ambiguous argument '\S+': unknown revision` · `fatal: No names found, cannot describe anything` · `fatal: bad revision` · `shallow update not allowed` · `fatal: no tag exactly matches`
- **Category:** config
- **Cause:** `actions/checkout` fetches one commit by default (`fetch-depth: 1`). Tools that diff against a base branch, compute versions from tags (setuptools-scm, semantic-release, GitVersion) or run `git describe` fail.
- **Fix:** `with: { fetch-depth: 0 }` (full history), or fetch what you need: `git fetch origin main --depth=50`, `fetch-tags: true`.
- **Verify:** `git log --oneline | wc -l && git describe --tags`

### GEN-15 · Tool missing after a runner image update
- **Signature:** `^\S*: (?:line \d+: )?\S+: command not found` · `Unable to locate executable file: \S+` · `/usr/bin/env: .\S+.: No such file or directory`
- **Category:** config
- **Cause:** Runner images change (`ubuntu-latest` moved to 24.04 and removed some preinstalled tools), or a setup step was skipped by an `if:`.
- **Fix:** Install tools explicitly with a pinned setup action instead of relying on preinstalled software. Pin `runs-on: ubuntu-22.04` temporarily if needed. Check the runner-images release notes.
- **Verify:** `which <tool> && <tool> --version` in a debug step

### GEN-16 · Invalid workflow file
- **Signature:** `Invalid workflow file` · `The workflow is not valid` · `Unrecognized named-value: '\w+'` · `Unexpected value '\w+'`
- **Category:** config
- **Cause:** A YAML or expression error. The run fails before any job starts and shows the error on the run page.
- **Fix:** Fix the reported line. Lint locally with `actionlint` and use the GitHub Actions editor extension.
- **Verify:** `actionlint .github/workflows/*.yml`

### GEN-17 · No runner available or runner errors
- **Signature:** `no enabled runners online` · `The hosted runner encountered an error` · `The job was not started because` · `Waiting for a runner to pick up this job`
- **Category:** infrastructure
- **Cause:** Self-hosted runner offline or mislabeled, `runs-on` label typo, billing or spending limits, or a GitHub incident.
- **Fix:** Check `runs-on` labels against available runners, billing status and githubstatus.com, then rerun.
- **Verify:** `gh api repos/{owner}/{repo}/actions/runners`

### GEN-18 · Cancelled by concurrency
- **Signature:** `Canceling since a higher priority waiting request for '[^']+' exists` · `cancel-in-progress`
- **Category:** config
- **Cause:** A `concurrency:` group with `cancel-in-progress: true` cancelled this run because a newer one started. Not a code failure.
- **Fix:** Nothing to fix in the code. Look at the newest run instead. Adjust the concurrency group if the wrong runs cancel each other.
- **Verify:** `gh run list --branch <branch> --limit 5`

### GEN-19 · Artifact or cache step failures
- **Signature:** `Failed to (?:save|restore): Cache service responded with \d+` · `Conflict: an artifact with this name already exists` · `No files were found with the provided path`
- **Category:** config
- **Cause:** `upload-artifact@v4` names must be unique per run (matrix jobs collide), the upload path doesn't exist because the build output moved, or the cache service hiccupped.
- **Fix:** Include matrix values in artifact names (`name: coverage-${{ matrix.os }}`), fix the path, and use `if-no-files-found: error` to catch path mistakes early.
- **Verify:** `ls -la <path>` in the step before upload

### GEN-20 · Runtime version not available on the runner image
- **Signature:** `The version '[^']+' with architecture '\w+' was not found for` · `Version [\w.*-]+ was not found in the local cache` · `Could not find a version that satisfied version spec`
- **Category:** config
- **Cause:** `ubuntu-latest` / `macos-latest` moved to a new image (e.g. Ubuntu 26.04 from Oct 2026) and the setup action has no prebuilt binary of an old or end-of-life runtime for it (Python 3.9 on Ubuntu 26.04), or the version spec doesn't exist at all.
- **Fix:** Keep the old version on an image that still has it, upgrade the runtime, or pin images for reproducibility:
  ```yaml
  strategy:
    matrix:
      os: [ubuntu-latest]
      python: ["3.12", "3.13"]
      include:
        - { os: ubuntu-24.04, python: "3.9" }   # EOL runtime: no build for newer images
  ```
  Check what exists in the action's versions manifest (e.g. `actions/python-versions`, `actions/node-versions`).
- **Verify:** Rerun the job, then check the "Successfully set up …" line in the setup step

### GEN-21 · Action or local action can't be resolved
- **Signature:** `Unable to resolve action .+, (?:repository not found|unable to find version)` · `Can't find 'action\.yml', 'action\.yaml' or 'Dockerfile' under` · `Did you forget to run actions/checkout before running your local action` · `is not allowed to be used in [\w.-]+/[\w.-]+\. Actions in this workflow must be`
- **Category:** config
- **Cause:** A typo in `uses:`, a tag that doesn't exist (`@v9`), a deleted or private action repository, a local action (`uses: ./.github/actions/x`) used before `actions/checkout`, or an organisation allow-list that blocks the action.
- **Fix:** Check the action's releases for the tag and pin to an existing major or a full commit SHA. Put `actions/checkout` before any local action. For allow-list errors, ask an org admin to allow the action, or use an approved alternative.
- **Verify:** `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` (or `gh release list -R <owner>/<repo>`)

### GEN-22 · Git "dubious ownership" in container jobs
- **Signature:** `detected dubious ownership in repository at` · `fatal: unsafe repository \('[^']+' is owned by someone else\)` · `To add an exception for this directory, call:`
- **Category:** config
- **Cause:** The job runs in a `container:` (often as root) while the workspace belongs to the runner user, or the other way round. Git 2.35.2+ refuses to work in a repository owned by another user, which breaks `git describe`, version tools and changelog generators.
- **Fix:** Mark the workspace as safe before git runs in that container:
  ```yaml
  - run: git config --global --add safe.directory "$GITHUB_WORKSPACE"
  ```
  `actions/checkout` does this for its own step only, not for later steps or other containers.
- **Verify:** `git -C "$GITHUB_WORKSPACE" status` in the same step

### GEN-23 · Private submodule or second repository can't be cloned
- **Signature:** `fatal: could not read Username for 'https://github\.com': terminal prompts disabled` · `fatal: clone of '[^']+' into submodule path '[^']+' failed` · `Failed to clone '[^']+'\. Retry scheduled` · `remote: Repository not found\.`
- **Category:** permissions
- **Cause:** `GITHUB_TOKEN` can only read the repository the workflow runs in. Private submodules, a second `actions/checkout` of another private repo, or private dependencies fetched with git (Go modules, npm git URLs) need their own credentials.
- **Fix:** Pass a token or deploy key that can read the other repo, preferably a short-lived GitHub App token:
  ```yaml
  - uses: actions/create-github-app-token@v1
    id: app
    with:
      app-id: ${{ vars.APP_ID }}
      private-key: ${{ secrets.APP_KEY }}
      owner: ${{ github.repository_owner }}
  - uses: actions/checkout@v4
    with:
      submodules: recursive
      token: ${{ steps.app.outputs.token }}
  ```
- **Verify:** `git clone --recurse-submodules <repo-url>` with only that token's access

### GEN-24 · `GITHUB_OUTPUT` / `GITHUB_ENV` format error
- **Signature:** `Unable to process file command '(?:env|output)' successfully` · `Invalid format '[^']*'` · `Matching delimiter not found '[^']+'`
- **Category:** config
- **Cause:** A value with newlines (release notes, JSON, a multi-line command output) was written as `echo "name=$value" >> "$GITHUB_OUTPUT"`. Each extra line is parsed as a new `key=value` pair and fails. A heredoc delimiter that also appears inside the value causes "Matching delimiter not found".
- **Fix:** Use the multi-line syntax with a random delimiter:
  ```bash
  delim="EOF_$(openssl rand -hex 8)"
  { echo "notes<<$delim"; cat notes.md; echo "$delim"; } >> "$GITHUB_OUTPUT"
  ```
- **Verify:** Rerun the step, then `echo '${{ steps.<id>.outputs.notes }}' | head` in a later step

### GEN-25 · Container action or service on a non-Linux runner
- **Signature:** `Container action is only supported on Linux` · `Container operations are only supported on Linux runners` · `Services? (?:are|is) only supported on Linux`
- **Category:** config
- **Cause:** A Docker-based action (`runs.using: docker`), a `container:` job or `services:` was used on `macos-*` or `windows-*`. GitHub-hosted macOS and Windows runners can't run Linux containers for these features.
- **Fix:** Run that job on `ubuntu-latest`, split OS-specific steps into separate jobs, or switch to a JavaScript / composite version of the action.
- **Verify:** Check `runs-on:` for the failing job, then rerun
