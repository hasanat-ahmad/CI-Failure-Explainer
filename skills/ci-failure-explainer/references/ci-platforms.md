# Logs from other CI systems

The digest, stack detection and pattern library work on **any** plain-text log. Only fetching is GitHub-specific. For other platforms, get the log text into a file, then run:

```bash
python3 scripts/ci_doctor.py --log-file failed.log --repo-dir .
```

| Platform | Get the failed job's log |
|---|---|
| GitLab CI | `glab ci trace <job-id> > failed.log` (or job page → "Show complete raw") |
| CircleCI | Job page → step → "Download step output", or the API `GET /api/v1.1/project/:vcs/:org/:repo/:build_num` → `output_url` |
| Jenkins | `curl -u user:token "$JOB_URL/<build>/consoleText" > failed.log` |
| Azure Pipelines | `az pipelines runs show` + run page → "Download logs" (zip of per-step logs) |
| Bitbucket Pipelines | Step → "Download raw log" |
| Buildkite | `bk job log <job-id> > failed.log`, or the job's "Download" button |
| Local build | `<cmd> 2>&1 | tee failed.log` |

Notes:

- The digest groups output into steps for GitHub (`gh run view --log-failed` and downloaded job logs), **GitLab** (`section_start`/`section_end` markers: `get_sources`, `step_script`, your own sections) and **Jenkins** (`[Pipeline] { (Stage)` blocks, parallel branches, timestamper prefixes). The failed step is the one holding the job's exit-code line: GitLab's `ERROR: Job failed` trailer is credited to the last section the script opened, and Jenkins' `ERROR: script returned exit code` to the last stage that ran. Other logs appear as one step called `log`, and that's fine.
- If the user **pastes** log text into the chat, write it to a temp file first and run `clean_logs.py` on it, rather than reasoning over the pasted text directly. You still get redaction and the digest.
- Workflow mapping (`workflow_steps.py`) only reads `.github/workflows`. For other systems, open their config (`.gitlab-ci.yml`, `.circleci/config.yml`, `Jenkinsfile`, `azure-pipelines.yml`, `bitbucket-pipelines.yml`) yourself to find the failing command.
