#!/usr/bin/env python3
"""Find a failed GitHub Actions run and download its failed-step logs.

Accepts a run URL / run ID, a PR number, or a branch (default: current branch).
Writes the raw failed logs to a temp file and prints a small JSON object with:

  * run metadata (workflow, event, branch, head SHA, attempt, URL)
  * failed jobs and their failed steps
  * the temp log path (feed it to clean_logs.py, never read it directly)
  * the last green run on the same workflow + branch, so you can diff green..red
  * a pass/fail history strip and "same SHA passed before" - strong flakiness signals

All network access goes through the GitHub CLI (`gh`). Standard library only.

Exit codes: 0 ok · 1 unexpected gh error · 2 environment problem (gh missing,
not authenticated, not a repo) · 3 run still in progress · 4 no failed run
found · 5 logs unavailable (expired or deleted).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _version import __version__

SCHEMA_VERSION = 1
FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
RUN_FIELDS = ("databaseId,number,workflowName,workflowDatabaseId,headBranch,headSha,status,conclusion,"
              "event,createdAt,url,displayTitle,attempt")
VIEW_FIELDS = ("databaseId,number,attempt,conclusion,status,event,headBranch,headSha,jobs,url,"
               "workflowName,workflowDatabaseId,createdAt,displayTitle")
# Transient failures worth retrying: GitHub 5xx, network resets, timeouts.
TRANSIENT = re.compile(r"(?i)http 5\d\d|timed? ?out|timeout|connection reset|unexpected eof|tls handshake|"
                       r"temporary failure|bad gateway|service unavailable")
RETRIES = int(os.environ.get("CI_EXPLAINER_GH_RETRIES", "2"))
RETRY_DELAY = float(os.environ.get("CI_EXPLAINER_RETRY_DELAY", "1.5"))
RUN_URL = re.compile(
    r"github\.com/(?P<repo>[^/\s]+/[^/\s]+)/actions/runs/(?P<run>\d+)"
    r"(?:/job/(?P<job>\d+))?(?:/attempts/(?P<attempt>\d+))?"
)
HISTORY_SYMBOL = {"success": "✓", "failure": "✗", "timed_out": "⏱", "cancelled": "⊘", "skipped": "·"}


class FetchError(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def run_cmd(args: Sequence[str], check: bool = True, timeout: int = 120) -> Tuple[int, str, str]:
    """Run a command; retry transient gh/network failures with exponential backoff."""
    for attempt in range(RETRIES + 1):
        last = attempt == RETRIES
        try:
            proc = subprocess.run(list(args), check=False, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise FetchError(f"'{args[0]}' is not installed or not on PATH.", 2) from None
        except subprocess.TimeoutExpired:
            if last:
                raise FetchError(f"Timed out after {timeout}s: {' '.join(args)}", 1) from None
            time.sleep(RETRY_DELAY * 2 ** attempt)
            continue
        if proc.returncode != 0 and not last and args[:1] == ["gh"] and TRANSIENT.search(proc.stderr or ""):
            time.sleep(RETRY_DELAY * 2 ** attempt)
            continue
        if check and proc.returncode != 0:
            raise FetchError(explain_gh_error(proc.stderr or proc.stdout, args), classify_gh_error(proc.stderr))
        return proc.returncode, proc.stdout, proc.stderr
    raise AssertionError("unreachable")  # pragma: no cover


def classify_gh_error(stderr: str) -> int:
    s = stderr.lower()
    if "http 410" in s or "log not found" in s or "expired" in s or "no longer available" in s:
        return 5
    if "auth" in s and ("login" in s or "token" in s or "401" in s):
        return 2
    if "not a git repository" in s or "no git remotes" in s or "none of the git remotes" in s:
        return 2
    return 1


def explain_gh_error(stderr: str, args: Sequence[str]) -> str:
    s = stderr.strip()
    low = s.lower()
    cmd = " ".join(args[:4])
    if "http 410" in low or "log not found" in low or "expired" in low:
        return ("Logs for this run are no longer available (GitHub keeps them for 90 days by default, "
                "or less if the repo sets a shorter retention). Re-run the workflow to produce fresh logs, "
                "or paste the log text and use clean_logs.py on it.")
    if "http 404" in low or "could not find" in low or "not found" in low:
        return f"GitHub returned 'not found' for `{cmd}`. Check the run/PR number and that you can access the repository.\n{s}"
    if "http 403" in low or "rate limit" in low:
        return f"GitHub refused the request (403 / rate limit). Wait a moment or check token scopes.\n{s}"
    if "not a git repository" in low or "no git remotes" in low:
        return "Not inside a git repository with a GitHub remote. cd into the repo, or pass --repo OWNER/NAME or a full run URL."
    return f"`{cmd}` failed:\n{s}"


def ensure_gh() -> None:
    if not shutil.which("gh"):
        raise FetchError(
            "GitHub CLI (gh) is not installed. Install it from https://cli.github.com "
            "(macOS: `brew install gh`, Debian/Ubuntu: `sudo apt install gh`), then run `gh auth login`.\n"
            "No gh? Paste the failing log into a file and run clean_logs.py on it instead.", 2)
    code, _, err = run_cmd(["gh", "auth", "status"], check=False)
    if code != 0:
        raise FetchError("gh is not authenticated. Run `gh auth login` (or set GH_TOKEN) and try again.\n" + err.strip(), 2)


def gh_json(args: Sequence[str], repo: Optional[str]) -> Any:
    full = ["gh", *args] + (["-R", repo] if repo else [])
    _, out, _ = run_cmd(full)
    try:
        return json.loads(out or "null")
    except ValueError:
        raise FetchError(f"Could not parse JSON from `{' '.join(full[:4])}`: {out[:200]!r}") from None


def current_branch() -> str:
    code, out, _ = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], check=False)
    if code != 0 or out.strip() != "true":
        raise FetchError("Not a git repository. cd into your project, or pass --run <url>, --pr, or --repo.", 2)
    _, out, _ = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = out.strip()
    if branch in ("", "HEAD"):
        raise FetchError("Detached HEAD: no current branch. Pass --branch, --pr, or --run.", 2)
    return branch


def pick_failed(runs: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Return the newest failed run plus notes about newer in-progress runs."""
    notes: List[str] = []
    failed = None
    for r in runs:
        if r.get("status") != "completed":
            if failed is None:
                notes.append(f"A newer run is still {r.get('status')}: "
                             f"{r.get('workflowName')} #{r.get('number')} ({r.get('url')}).")
            continue
        if r.get("conclusion") in FAILED_CONCLUSIONS and failed is None:
            failed = r
    return failed, notes


Resolved = Tuple[str, Optional[str], Optional[str], Optional[str], List[str], List[Dict[str, Any]]]


def resolve_run(args: argparse.Namespace) -> Resolved:
    """Return (run_id, repo, job_id, attempt, notes, other_failed_runs)."""
    repo = args.repo
    if args.run:
        m = RUN_URL.search(args.run)
        if m:
            return m.group("run"), m.group("repo"), m.group("job"), m.group("attempt"), [], []
        if args.run.isdigit():
            return args.run, repo, None, None, [], []
        raise FetchError(f"--run must be a run ID or a GitHub Actions run URL, got {args.run!r}", 2)

    notes: List[str] = []
    if args.pr:
        pr = gh_json(["pr", "view", str(args.pr), "--json", "number,headRefName,headRefOid,url,isCrossRepository"], repo)
        sha = pr["headRefOid"]
        try:
            runs = gh_json(["run", "list", "--commit", sha, "--limit", "30", "--json", RUN_FIELDS], repo)
        except FetchError:  # older gh without --commit
            runs = [r for r in gh_json(["run", "list", "--branch", pr["headRefName"], "--limit", "30",
                                        "--json", RUN_FIELDS], repo) if r.get("headSha") == sha]
        if pr.get("isCrossRepository"):
            notes.append("PR comes from a fork: repository secrets are NOT available to its pull_request runs.")
        where = f"PR #{pr['number']} (head {sha[:7]})"
    else:
        branch = args.branch or current_branch()
        runs = gh_json(["run", "list", "--branch", branch, "--limit", "30", "--json", RUN_FIELDS], repo)
        where = f"branch '{branch}'"

    if not runs:
        raise FetchError(f"No workflow runs found for {where}. Has CI run yet? Check `gh run list`.", 4)
    failed, more_notes = pick_failed(runs)
    notes += more_notes
    if failed is None:
        if any(r.get("status") != "completed" for r in runs):
            raise FetchError(f"No failed run for {where} yet; a run is still in progress. Wait for it: "
                             f"`gh run watch {runs[0]['databaseId']}`.", 3)
        raise FetchError(f"No failed runs among the latest {len(runs)} runs for {where}. Latest conclusion: "
                         f"{runs[0].get('conclusion')}.", 4)
    others = [
        {"id": r["databaseId"], "workflow": r.get("workflowName"), "url": r.get("url")}
        for r in runs
        if r is not failed and r.get("status") == "completed" and r.get("conclusion") in FAILED_CONCLUSIONS
        and r.get("headSha") == failed.get("headSha")
    ]
    return str(failed["databaseId"]), repo, None, None, notes, others


def history_for(run: Dict[str, Any], repo: Optional[str]) -> Dict[str, Any]:
    wf = run.get("workflowDatabaseId") or run.get("workflowName")
    branch = run.get("headBranch")
    if not wf or not branch:
        return {}
    try:
        runs = gh_json(["run", "list", "--workflow", str(wf), "--branch", branch, "--limit", "15",
                        "--json", RUN_FIELDS], repo)
    except FetchError:
        return {}
    completed = [r for r in runs if r.get("status") == "completed"]
    created = run.get("createdAt") or ""
    last_success = next(
        (r for r in completed if r.get("conclusion") == "success" and (r.get("createdAt") or "") < created),
        None,
    )
    same_sha_ok = any(r.get("conclusion") == "success" and r.get("headSha") == run.get("headSha") for r in completed)
    strip = "".join(HISTORY_SYMBOL.get(r.get("conclusion") or "", "?") for r in completed)
    return {
        "last_success": None if not last_success else {
            "id": last_success["databaseId"], "head_sha": last_success.get("headSha"),
            "created_at": last_success.get("createdAt"), "url": last_success.get("url"),
        },
        "same_sha_succeeded": same_sha_ok,
        "history": strip,
        "history_legend": "newest first: ✓ success ✗ failure ⏱ timed out ⊘ cancelled",
    }


def fetch_logs(run_id: str, repo: Optional[str], job_id: Optional[str], attempt: Optional[str],
               failed_jobs: List[Dict[str, Any]]) -> str:
    base = ["gh", "run", "view"] + (["--job", job_id] if job_id else [run_id])
    if attempt:
        base += ["--attempt", attempt]
    if repo:
        base += ["-R", repo]
    _, out, _ = run_cmd([*base, "--log-failed"], timeout=300)
    if out.strip():
        return out
    # --log-failed can be empty when a job died before any step logged (e.g. setup
    # failure or cancellation). Fall back to the full log of each failed job.
    chunks = []
    for job in failed_jobs[:3]:
        cmd = ["gh", "run", "view", "--job", str(job["id"]), "--log"] + (["-R", repo] if repo else [])
        code, jout, _ = run_cmd(cmd, check=False, timeout=300)
        if code == 0 and jout.strip():
            chunks.append(jout)
    if not chunks:
        raise FetchError("GitHub returned no log text for the failed jobs. Open the run URL to check whether the job "
                         "ran at all (runner assignment or startup failures produce no logs).", 5)
    return "\n".join(chunks)


def summarise_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    failed = []
    for job in jobs or []:
        if job.get("conclusion") not in FAILED_CONCLUSIONS:
            continue
        failed.append({
            "id": job.get("databaseId"),
            "name": job.get("name"),
            "conclusion": job.get("conclusion"),
            "url": job.get("url"),
            "failed_steps": [
                {"number": s.get("number"), "name": s.get("name"), "conclusion": s.get("conclusion")}
                for s in job.get("steps") or [] if s.get("conclusion") in FAILED_CONCLUSIONS
            ],
        })
    return failed


def fetch(args: argparse.Namespace) -> Dict[str, Any]:
    ensure_gh()
    run_id, repo, job_id, attempt, notes, others = resolve_run(args)
    view = ["run", "view", run_id, "--json", VIEW_FIELDS] + (["--attempt", attempt] if attempt else [])
    run = gh_json(view, repo)

    if run.get("status") != "completed":
        raise FetchError(f"Run {run_id} is still {run.get('status')}. Logs are complete only when it finishes: "
                         f"`gh run watch {run_id}`.", 3)
    if run.get("conclusion") not in FAILED_CONCLUSIONS:
        notes.append(f"Run conclusion is '{run.get('conclusion')}', not a failure.")

    failed_jobs = summarise_jobs(run.get("jobs") or [])
    if job_id:
        failed_jobs = [j for j in failed_jobs if str(j["id"]) == job_id] or failed_jobs
    if (run.get("attempt") or 1) > 1:
        notes.append(f"This is attempt #{run['attempt']}: the run was re-run, compare with earlier attempts.")

    log_text = fetch_logs(run_id, repo, job_id, attempt, failed_jobs)
    out_dir = args.out_dir or tempfile.mkdtemp(prefix="ci-failure-explainer-")
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, f"run-{run_id}-failed.log")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(log_text)
    with contextlib.suppress(OSError):
        os.chmod(log_path, 0o600)  # raw logs may contain unmasked secrets

    hist = history_for(run, repo)
    if hist.get("same_sha_succeeded"):
        notes.append("The SAME commit passed in another run of this workflow: strong flakiness / environment signal.")
    last = (hist.get("last_success") or {}).get("head_sha")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": __version__,
        "repo": repo,
        "run": {
            "id": run.get("databaseId"), "number": run.get("number"), "url": run.get("url"),
            "workflow": run.get("workflowName"), "workflow_id": run.get("workflowDatabaseId"),
            "title": run.get("displayTitle"), "event": run.get("event"), "branch": run.get("headBranch"),
            "head_sha": run.get("headSha"), "attempt": run.get("attempt"), "conclusion": run.get("conclusion"),
            "created_at": run.get("createdAt"),
        },
        "failed_jobs": failed_jobs,
        "log_path": log_path,
        "log_lines": log_text.count("\n") + 1,
        **hist,
        "other_failed_runs": others,
        "notes": notes,
        "next": [
            f"python3 {os.path.join(script_dir, 'clean_logs.py')} {log_path}",
            *([f"git diff --stat {last}..{run.get('headSha')}"] if last and last != run.get("headSha") else []),
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Locate a failed GitHub Actions run and save its failed-step logs (via gh).",
        epilog="Examples:\n  fetch_failed_logs.py                      # latest failure on current branch\n"
               "  fetch_failed_logs.py --pr 42\n"
               "  fetch_failed_logs.py --run https://github.com/o/r/actions/runs/123/job/456",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--run", help="run ID or run/job URL")
    sel.add_argument("--pr", type=int, help="pull request number")
    sel.add_argument("--branch", help="branch name (default: current git branch)")
    ap.add_argument("--repo", help="OWNER/NAME (default: repo of the current directory)")
    ap.add_argument("--out-dir", help="directory for the raw log (default: a private temp dir)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)
    try:
        result = fetch(args)
    except FetchError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(exc), "exit_code": exc.code}, indent=2))
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
