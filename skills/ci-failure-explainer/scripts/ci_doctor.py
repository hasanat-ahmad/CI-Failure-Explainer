#!/usr/bin/env python3
"""One command from "CI is red" to a compact triage brief.

  fetch (gh) -> redact + digest -> detect stack -> match known patterns
             -> map to workflow step -> flakiness signals -> brief

  ci_doctor.py                         # latest failed run on the current branch
  ci_doctor.py --pr 42
  ci_doctor.py --run https://github.com/o/r/actions/runs/123
  ci_doctor.py --log-file pasted.log   # any CI system, no gh needed

Prints a markdown brief (~200 lines max) and writes a bundle directory with
digest.md, digest.json, matches.json, workflow.json, repro.sh and meta.json.
The raw log stays in the bundle with 0600 permissions; never read it directly.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _version import __version__
import clean_logs
import detect_stack
import fetch_failed_logs
import match_patterns
import workflow_steps

SCHEMA_VERSION = 1
T = TypeVar("T")

FLAKY_WORDS = re.compile(
    r"(?i)\b(?:timed? ?out|timeout|ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|socket hang up|503 Service|"
    r"502 Bad Gateway|rate limit(?:ed)?|flaky|race condition|data race|deadlock|connection reset|TLS handshake)\b"
)
CATEGORY_HINTS = [
    ("lint", r"(?i)\b(eslint|prettier|ruff|black|flake8|mypy|dart format|flutter analyze|golangci|clippy|rustfmt|"
             r"would reformat)\b"),
    ("dependency", r"(?i)ERESOLVE|lock ?file|version solving|ResolutionImpossible|No matching distribution|Could not resolve"),
    ("test", r"(?i)\b(tests? failed|FAILED \S+::|● |--- FAIL|AssertionError|expect\()"),
    ("build", r"(?i)\b(compil|error TS\d+|cannot find symbol|failed to solve|build failed)"),
    ("infrastructure", r"(?i)No space left|heap out of memory|exit code 137|Killed|operation was canceled|runner"),
    ("config", r"(?i)secret|environment variable|Input required|not set|credentials"),
    ("permissions", r"(?i)Resource not accessible|403|permission denied"),
]


def flakiness_signals(meta: Optional[Dict[str, Any]], digest_md: str, matches: Sequence[Any]) -> List[str]:
    signals = []
    if meta:
        if meta.get("same_sha_succeeded"):
            signals.append("the same commit passed in another run")
        attempt = (meta.get("run") or {}).get("attempt") or 1
        if attempt > 1:
            signals.append(f"run is attempt #{attempt}")
        hist = meta.get("history") or ""
        if len(hist) >= 4 and sum(1 for a, b in zip(hist, hist[1:]) if a != b) >= 3:
            signals.append(f"history alternates pass/fail ({hist})")
    if FLAKY_WORDS.search(digest_md):
        signals.append("error mentions timing/network")
    if matches and matches[0].entry.category == "flaky":
        signals.append(f"best match {matches[0].entry.id} is a known flaky pattern")
    return signals


def guess_category(digest_md: str, matches: Sequence[Any]) -> str:
    if matches and matches[0].confidence == "high":
        return matches[0].entry.category
    for cat, rx in CATEGORY_HINTS:
        if re.search(rx, digest_md):
            return cat
    return matches[0].entry.category if matches else "unknown"


def shift_headings(md: str) -> str:
    """Nest the digest under the brief's headings (# -> ###)."""
    out, in_code = [], False
    for line in md.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        out.append("##" + line if not in_code and line.startswith("#") else line)
    return "\n".join(out)


class Phases:
    """Run optional pipeline phases. A failure becomes a warning in the brief, never a crash."""

    def __init__(self) -> None:
        self.warnings: List[str] = []

    def run(self, name: str, fn: Callable[[], T], default: T) -> T:
        try:
            return fn()
        except Exception as exc:  # best effort by design: partial results beat no results
            self.warnings.append(f"{name} skipped ({type(exc).__name__}: {exc})")
            return default


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def map_workflow_step(repo_dir: Path, meta: Optional[Dict[str, Any]], digest: Any,
                      check_local: bool) -> Optional[Dict[str, Any]]:
    if not (repo_dir / ".github" / "workflows").is_dir():
        return None
    job = step = None
    if meta and meta.get("failed_jobs"):
        fj = meta["failed_jobs"][0]
        job = fj.get("name")
        step = (fj.get("failed_steps") or [{}])[0].get("name")
    else:
        failed = [st for st in digest.steps if st.failed]
        if failed:  # downloaded full-job logs carry no job name, but the step name still maps
            job = failed[0].job if failed[0].job != "log" else None
            step = failed[0].step if failed[0].step != "log" else None
    if not (job or step):
        return None
    wf = workflow_steps.find_step(repo_dir, (meta or {}).get("run", {}).get("workflow"), job, step)
    if "error" not in wf and check_local:
        wf["local_versions"] = workflow_steps.local_versions(wf["toolchain"])
    for t in wf.get("toolchain", []):
        t.pop("local_cmd", None)
    return wf


def run(args: argparse.Namespace) -> int:
    created = not args.out_dir
    out_dir = Path(args.out_dir or tempfile.mkdtemp(prefix="ci-doctor-"))  # mkdtemp is 0700
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = Path(args.repo_dir)
    phases = Phases()

    meta: Optional[Dict[str, Any]] = None
    if args.log_file:
        log_path = Path(args.log_file)
        if not log_path.is_file():
            print(f"error: --log-file {log_path} not found", file=sys.stderr)
            return 2
        source = log_path.name
    else:
        ns = argparse.Namespace(run=args.run, pr=args.pr, branch=args.branch, repo=args.repo, out_dir=str(out_dir))
        try:
            meta = fetch_failed_logs.fetch(ns)
        except fetch_failed_logs.FetchError as exc:
            print(f"# CI Doctor\n\n**Could not fetch the failed run:** {exc}\n\n"
                  "Fallback: save the failing log text to a file and run "
                  "`ci_doctor.py --log-file <file>`.")
            return exc.code
        log_path = Path(meta["log_path"])
        source = f"run {meta['run']['id']} ({meta['run'].get('workflow')})"

    # Core phase: without a digest there is nothing safe to show, so failures here are fatal.
    try:
        raw = clean_logs.read_input(str(log_path))
        digest = clean_logs.digest_text(raw, source, max_lines=args.max_lines, context=args.context)
    except (OSError, ValueError) as exc:
        print(f"error: could not read or digest {log_path}: {exc}", file=sys.stderr)
        return 1
    del raw
    if args.discard_raw and meta:
        with contextlib.suppress(OSError):
            log_path.unlink()
        meta["log_path"] = None
    digest_md = clean_logs.render_markdown(digest)
    write_private(out_dir / "digest.md", digest_md)
    write_private(out_dir / "digest.json", clean_logs.render_json(digest))

    # Optional phases: each degrades to a warning.
    no_stack: Dict[str, Any] = {"stacks": [], "references": ["stack-general.md"], "toolchain": {}}
    stack_info = phases.run("stack detection", lambda: detect_stack.detect(repo_dir, digest_md), no_stack)
    stacks = [str(s["stack"]) for s in stack_info.get("stacks", [])]
    matches = phases.run(
        "pattern matching",
        lambda: match_patterns.match(digest_md, match_patterns.load_library(), stacks, top=args.top), [])
    write_private(out_dir / "matches.json", match_patterns.render_json(matches))
    wf = phases.run("workflow mapping",
                    lambda: map_workflow_step(repo_dir, meta, digest, check_local=not args.no_local), None)
    if wf:
        write_private(out_dir / "workflow.json", json.dumps(wf, indent=2, default=str))
        if wf.get("repro_script"):
            write_private(out_dir / "repro.sh", wf["repro_script"] + "\n", mode=0o700)

    signals = flakiness_signals(meta, digest_md, matches)
    category = guess_category(digest_md, matches)
    if meta:
        write_private(out_dir / "meta.json", json.dumps(meta, indent=2))

    brief = render_brief(meta=meta, digest_md=digest_md, stack_info=stack_info, matches=matches, wf=wf,
                         signals=signals, category=category, out_dir=out_dir, warnings=phases.warnings)
    write_private(out_dir / "brief.md", brief)
    if args.format == "json":
        print(json.dumps({
            "schema_version": SCHEMA_VERSION, "tool_version": __version__,
            "bundle": str(out_dir), "bundle_is_temp": created, "category_guess": category,
            "flaky_signals": signals, "warnings": phases.warnings, "meta": meta, "stacks": stack_info,
            "workflow_step": wf, "matches": json.loads(match_patterns.render_json(matches)),
            "digest": json.loads(clean_logs.render_json(digest)),
        }, indent=2, default=str))
    else:
        print(brief)
    return 0


def _run_lines(meta: Dict[str, Any]) -> List[str]:
    r = meta["run"]
    o = [f"- **Run:** [{r.get('workflow')} #{r.get('number')}]({r.get('url')}) · event `{r.get('event')}` · "
         f"branch `{r.get('branch')}` · sha `{(r.get('head_sha') or '')[:7]}` · attempt {r.get('attempt')}"]
    for j in meta.get("failed_jobs", [])[:4]:
        steps = ", ".join(f"`{s['name']}`" for s in j.get("failed_steps", [])) or "(no step marked failed)"
        o.append(f"- **Failed job:** `{j.get('name')}` ({j.get('conclusion')}) → {steps}")
    last = meta.get("last_success")
    green = f"`{str(last['head_sha'])[:7]}`" if last else "none in recent runs"
    o.append(f"- **History:** {meta.get('history') or 'n/a'} (newest first) · last green: {green}")
    o += [f"- **Also failing on this commit:** {x['workflow']} ({x['url']})" for x in meta.get("other_failed_runs", [])[:3]]
    o += [f"- **Note:** {n}" for n in meta.get("notes", [])]
    return o


def _workflow_lines(wf: Dict[str, Any]) -> List[str]:
    if "error" in wf:
        return ["## Failing step in the workflow", f"- {wf['error']}", ""]
    o = ["## Failing step in the workflow",
         f"- `{wf['workflow_file']}` › job `{wf['job_id']}` › step {wf['step_index']} `{wf['step_name']}` "
         f"(match {wf['match_confidence']}) · runs-on `{wf.get('runs_on')}`"]
    if wf.get("matrix"):
        o.append(f"- **Matrix:** `{json.dumps(wf['matrix'], default=str)[:200]}`")
    if wf.get("run"):
        o += ["- **Command:**", "```bash", str(wf["run"]).strip()[:1500], "```"]
    if wf.get("secrets_referenced"):
        o.append(f"- **Secrets used by this job:** {', '.join(wf['secrets_referenced'])} "
                 "(unavailable to fork PRs and Dependabot runs)")
    versions = wf.get("local_versions") or [
        {"tool": t["tool"], "ci": t["pins"], "local": "not checked"} for t in wf.get("toolchain", [])]
    o += [f"- **{lv['tool']}:** CI {json.dumps(lv['ci'])} · local `{lv['local']}`" for lv in versions]
    return [*o, ""]


def render_brief(*, meta: Optional[Dict[str, Any]], digest_md: str, stack_info: Dict[str, Any],
                 matches: Sequence[Any], wf: Optional[Dict[str, Any]], signals: List[str], category: str,
                 out_dir: Path, warnings: Sequence[str] = ()) -> str:
    o: List[str] = ["# CI Doctor triage brief", ""]
    if meta:
        o += _run_lines(meta)
    o.append(f"- **Category guess:** {category}")
    o.append(f"- **Flakiness signals:** {len(signals)}" + (f" ({'; '.join(signals)})" if signals else ""))
    present = [f for f in ("digest.md", "matches.json", "workflow.json", "repro.sh") if (out_dir / f).exists()]
    o.append(f"- **Bundle:** `{out_dir}` ({', '.join(present)})")
    o += [f"- **Warning:** {w}" for w in warnings]
    o += ["", shift_headings(digest_md), ""]

    o.append("## Stack")
    for s in stack_info.get("stacks", [])[:4]:
        ev = "; ".join(str(e) for e in s.get("evidence", [])[:2])
        o.append(f"- **{s['stack']}** ({s['confidence']}): {ev} → `references/{s['reference']}`")
    if not stack_info.get("stacks"):
        o.append("- nothing detected → `references/stack-general.md`")
    tc = stack_info.get("toolchain") or {}
    if tc:
        o.append("- **Pinned locally:** " + "; ".join(f"{k}: {v}" for k, v in list(tc.items())[:6]))
    o += ["", match_patterns.render_markdown(matches[:3]), ""]
    if wf:
        o += _workflow_lines(wf)

    o.append("## Next")
    last = (meta or {}).get("last_success") or {}
    head = ((meta or {}).get("run") or {}).get("head_sha")
    if last.get("head_sha") and head and last["head_sha"] != head:
        o.append(f"- What changed since green: `git diff --stat {last['head_sha'][:12]}..{head[:12]}`")
    if (out_dir / "repro.sh").exists():
        o.append(f"- Reproduce locally: `bash {out_dir / 'repro.sh'}` (review it first)")
    if (meta or {}).get("log_path"):
        o.append(f"- Drill into the log safely: `clean_logs.py {meta['log_path']} --slice A:B` or `--grep REGEX`")
    o.append("- Then follow references/diagnostic-method.md and report with references/report-template.md")
    return "\n".join(o)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Diagnose a failed CI run in one command: fetch, redact, digest, detect, match, map.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  ci_doctor.py\n  ci_doctor.py --pr 42\n  ci_doctor.py --log-file build.log --repo-dir .\n\n"
               "Exit codes: 0 ok · 1 unexpected error · 2 environment/usage · 3 run in progress · "
               "4 no failed run · 5 logs unavailable",
    )
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--run", help="run ID or run/job URL")
    sel.add_argument("--pr", type=int, help="pull request number")
    sel.add_argument("--branch", help="branch (default: current)")
    sel.add_argument("--log-file", help="diagnose a saved/pasted log instead of fetching (any CI system)")
    ap.add_argument("--repo", help="OWNER/NAME for gh (default: current repo)")
    ap.add_argument("--repo-dir", default=".", help="local checkout to inspect (default: .)")
    ap.add_argument("--out-dir", help="bundle directory (default: new private temp dir)")
    ap.add_argument("--max-lines", type=int, default=120, help="digest line budget (default 120)")
    ap.add_argument("--context", type=int, default=15)
    ap.add_argument("--top", type=int, default=5, help="number of pattern matches to keep")
    ap.add_argument("--no-local", action="store_true", help="skip local toolchain version checks")
    ap.add_argument("--discard-raw", action="store_true",
                    help="delete the fetched raw log after digesting (recommended in CI)")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)
    if args.max_lines < 20:
        ap.error("--max-lines must be at least 20")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
