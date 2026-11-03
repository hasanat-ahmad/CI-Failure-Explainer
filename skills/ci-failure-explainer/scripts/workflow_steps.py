#!/usr/bin/env python3
"""Map a failed job/step back to the workflow file: exact command, toolchain, secrets.

Given the workflow, job and step names from fetch_failed_logs.py (or just a step
name), this reads .github/workflows/*.yml and reports:

  * the exact `run:` script of the failing step and its working directory
  * a copy-pasteable local reproduction script
  * toolchain pins from setup-* actions (node/python/java/flutter/go/rust/...)
  * matrix values, `runs-on`, container image
  * secrets and env vars the job references (missing secrets are a top cause)
  * optional local toolchain versions, to show CI-vs-local drift (--check-local)

Includes a small YAML-subset parser (block maps, lists, block scalars, flow
lists) so it needs only the standard library. Python 3.9+.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _version import __version__

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# Minimal YAML subset parser (enough for GitHub Actions workflows)
# --------------------------------------------------------------------------- #

_KEY = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s#'"\-][^:#]*?|-[^\s:][^:#]*?)\s*:(?:\s+(?P<rest>.*)|\s*$)""")
_BLOCK_INDICATOR = re.compile(r"^[|>][+-]?\d*$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_comment(value: str) -> str:
    if value[:1] in ("'", '"'):
        q = value[0]
        end = value.find(q, 1)
        return value[: end + 1] if end > 0 else value
    m = re.search(r"\s#", value)
    return value[: m.start()] if m else value


def _scalar(value: str) -> Any:
    v = _strip_comment(value).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in re.split(r",\s*", inner)] if inner else []
    return v


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines = text.replace("\t", "  ").splitlines()
        self.i = 0

    def _skip(self) -> None:
        while self.i < len(self.lines):
            s = self.lines[self.i].strip()
            if s == "" or s.startswith("#") or s == "---":
                self.i += 1
            else:
                break

    def _eof(self) -> bool:
        self._skip()
        return self.i >= len(self.lines)

    def parse(self) -> Any:
        if self._eof():
            return None
        return self.block(_indent(self.lines[self.i]))

    def block(self, ind: int) -> Any:
        if self._eof():
            return None
        content = self.lines[self.i].strip()
        if content == "-" or content.startswith("- "):
            return self.seq(ind)
        return self.mapping(ind)

    def seq(self, ind: int) -> List[Any]:
        items: List[Any] = []
        while not self._eof():
            line = self.lines[self.i]
            content = line.strip()
            if _indent(line) != ind or not (content == "-" or content.startswith("- ")):
                break
            rest = content[1:].strip()
            if not rest:
                self.i += 1
                items.append(self.block(ind + 2) if not self._eof() and _indent(self.lines[self.i]) > ind else None)
            elif _KEY.match(rest) and not rest.startswith(("{", "[")):
                col = ind + (len(content) - len(rest))
                self.lines[self.i] = " " * col + rest  # "- key: v" -> map at the item's column
                items.append(self.mapping(col))
            else:
                self.i += 1
                items.append(_scalar(rest))
        return items

    def mapping(self, ind: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while not self._eof():
            line = self.lines[self.i]
            cur, content = _indent(line), line.strip()
            if cur < ind or (cur == ind and (content == "-" or content.startswith("- "))):
                break
            if cur > ind:
                self.i += 1  # stray over-indented line; tolerate
                continue
            m = _KEY.match(content)
            if not m:
                self.i += 1
                continue
            key = m.group("key").strip().strip("'\"")
            rest = _strip_comment(m.group("rest") or "").strip()
            self.i += 1
            if _BLOCK_INDICATOR.match(rest):
                result[key] = self.block_scalar(ind, rest)
            elif rest == "":
                if self._eof():
                    result[key] = None
                    break
                nxt = self.lines[self.i]
                ni, nc = _indent(nxt), nxt.strip()
                if ni > ind or (ni == ind and (nc == "-" or nc.startswith("- "))):
                    result[key] = self.block(ni)
                else:
                    result[key] = None
            else:
                result[key] = _scalar(m.group("rest") or "")
        return result

    def block_scalar(self, ind: int, indicator: str) -> str:
        collected: List[str] = []
        block_ind: Optional[int] = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip():
                collected.append("")
                self.i += 1
                continue
            li = _indent(line)
            if block_ind is None:
                if li <= ind:
                    break
                block_ind = li
            if li < block_ind:
                break
            collected.append(line[block_ind:])
            self.i += 1
        while collected and collected[-1] == "":
            collected.pop()
        if indicator.startswith(">"):
            text, para = [], []
            for c in collected:
                if c == "":
                    text.append(" ".join(para))
                    para = []
                else:
                    para.append(c)
            text.append(" ".join(para))
            return "\n".join(text)
        return "\n".join(collected)


def parse_yaml(text: str) -> Any:
    return _Parser(text).parse()


# --------------------------------------------------------------------------- #
# Workflow analysis
# --------------------------------------------------------------------------- #

SETUP_ACTIONS: Dict[str, Tuple[str, Sequence[str], List[str]]] = {
    # action prefix: (tool, version inputs, local version command)
    "actions/setup-node": ("node", ("node-version", "node-version-file"), ["node", "--version"]),
    "actions/setup-python": ("python", ("python-version", "python-version-file"), ["python3", "--version"]),
    "actions/setup-java": ("java", ("java-version", "distribution"), ["java", "-version"]),
    "subosito/flutter-action": ("flutter", ("flutter-version", "channel", "flutter-version-file"), ["flutter", "--version"]),
    "actions/setup-go": ("go", ("go-version", "go-version-file"), ["go", "version"]),
    "dtolnay/rust-toolchain": ("rust", ("toolchain",), ["rustc", "--version"]),
    "actions-rust-lang/setup-rust-toolchain": ("rust", ("toolchain",), ["rustc", "--version"]),
    "ruby/setup-ruby": ("ruby", ("ruby-version",), ["ruby", "--version"]),
    "pnpm/action-setup": ("pnpm", ("version",), ["pnpm", "--version"]),
    "astral-sh/setup-uv": ("uv", ("version",), ["uv", "--version"]),
    "gradle/actions/setup-gradle": ("gradle", ("gradle-version",), ["gradle", "--version"]),
    "oven-sh/setup-bun": ("bun", ("bun-version",), ["bun", "--version"]),
    "actions/setup-dotnet": ("dotnet", ("dotnet-version", "global-json-file"), ["dotnet", "--version"]),
    "shivammathur/setup-php": ("php", ("php-version", "php-version-file", "extensions"), ["php", "--version"]),
}
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
VERSION_RX = re.compile(r"\d+\.\d+")


def step_display_name(step: Dict[str, Any]) -> str:
    """Reproduce how GitHub names a step in the UI and in gh logs."""
    if step.get("name"):
        return str(step["name"])
    if step.get("uses"):
        return f"Run {step['uses']}"
    run = str(step.get("run") or "").strip().splitlines()
    return f"Run {run[0]}" if run else "Run"


def step_log_titles(step: Dict[str, Any]) -> List[str]:
    """Every name a step can appear under: its display name (gh --log-failed prefix, UI)
    and the "Run <command>" / "Run <action>" group title used in downloaded job logs,
    which GitHub shows even when the step has a `name:`."""
    titles = [step_display_name(step)]
    if step.get("uses"):
        titles.append(f"Run {step['uses']}")
    run = str(step.get("run") or "").strip().splitlines()
    if run:
        titles.append(f"Run {run[0]}")
    return titles


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", EXPR.sub("*", s or "")).strip().lower()


def _name_matches(pattern: str, actual: str) -> float:
    """Similarity 0..1 between a workflow name (may contain ${{ }}) and a reported name."""
    p, a = _norm(pattern), _norm(actual)
    if not p or not a:
        return 0.0
    if p == a:
        return 1.0
    if "*" in p:
        rx = "^" + ".*".join(re.escape(part) for part in p.split("*")) + "$"
        if re.match(rx, a):
            return 0.95
    a_base = re.sub(r"\s*\(.*\)$", "", a)          # strip matrix suffix: "test (ubuntu, 18)"
    if p == a_base:
        return 0.9
    if a.startswith(p) or p.startswith(a):
        return 0.8
    return difflib.SequenceMatcher(None, p, a).ratio() * 0.7


def load_workflows(repo: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    out = []
    wf_dir = repo / ".github" / "workflows"
    for path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        try:
            data = parse_yaml(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # parser is best-effort; never crash the diagnosis
            data = {"__error__": str(exc)}
        if isinstance(data, dict):
            out.append((path, data))
    return out


def find_step(repo: Path, workflow: Optional[str], job: Optional[str], step: Optional[str]) -> Dict[str, Any]:
    workflows = load_workflows(repo)
    if not workflows:
        return {"error": f"No workflow files under {repo / '.github' / 'workflows'}"}
    best: Tuple[float, Any] = (-1.0, None)
    for path, data in workflows:
        wf_name = str(data.get("name") or path.stem)
        wf_score = _name_matches(wf_name, workflow) if workflow else 0.5
        if workflow and wf_score < 0.5 and _name_matches(path.name, workflow) < 0.5:
            continue
        jobs = data.get("jobs") or {}
        if not isinstance(jobs, dict):
            continue
        for job_id, jd in jobs.items():
            if not isinstance(jd, dict):
                continue
            j_score = max(_name_matches(str(jd.get("name") or job_id), job), _name_matches(job_id, job)) if job else 0.5
            steps = jd.get("steps") or []
            for idx, sd in enumerate(steps if isinstance(steps, list) else []):
                if not isinstance(sd, dict):
                    continue
                if step:
                    s_score = max(_name_matches(t, step) for t in step_log_titles(sd))
                else:
                    s_score = 0.3 if sd.get("run") else 0.0
                total = wf_score + 2 * j_score + 3 * s_score
                if total > best[0]:
                    best = (total, (path, data, job_id, jd, idx, sd, s_score))
    if best[1] is None:
        return {"error": "No matching job/step found in workflow files."}
    path, data, job_id, jd, idx, sd, s_score = best[1]
    return describe(repo=repo, path=path, data=data, job_id=job_id, jd=jd, idx=idx, sd=sd,
                    confidence=round(min(best[0] / 6.0, 1.0), 2))


def describe(*, repo: Path, path: Path, data: Dict[str, Any], job_id: str, jd: Dict[str, Any],
             idx: int, sd: Dict[str, Any], confidence: float) -> Dict[str, Any]:
    steps = jd.get("steps") or []
    defaults = ((jd.get("defaults") or {}).get("run") or {}) if isinstance(jd.get("defaults"), dict) else {}
    wf_defaults = ((data.get("defaults") or {}).get("run") or {}) if isinstance(data.get("defaults"), dict) else {}
    wd = sd.get("working-directory") or defaults.get("working-directory") or wf_defaults.get("working-directory")
    shell = sd.get("shell") or defaults.get("shell") or wf_defaults.get("shell")

    toolchain = []
    for prev in steps[: idx + 1]:
        if not isinstance(prev, dict) or not prev.get("uses"):
            continue
        uses = str(prev["uses"])
        for prefix, (tool, inputs, cmd) in SETUP_ACTIONS.items():
            if uses.startswith(prefix):
                with_ = prev.get("with") or {}
                pins = {k: with_.get(k) for k in inputs if isinstance(with_, dict) and with_.get(k) is not None}
                toolchain.append({"tool": tool, "action": uses, "pins": pins, "local_cmd": cmd})

    job_text = json.dumps(jd)
    wf_env = data.get("env") if isinstance(data.get("env"), dict) else {}
    job_env = jd.get("env") if isinstance(jd.get("env"), dict) else {}
    step_env = sd.get("env") if isinstance(sd.get("env"), dict) else {}
    secrets = sorted(set(re.findall(r"secrets\.([A-Za-z0-9_]+)", job_text + json.dumps(wf_env))))
    variables = sorted(set(re.findall(r"vars\.([A-Za-z0-9_]+)", job_text + json.dumps(wf_env))))
    env_names = sorted(set(list(wf_env) + list(job_env) + list(step_env)))
    triggers = data.get("on")
    if isinstance(triggers, dict):
        trigger_names = list(triggers)
    else:
        trigger_names = [triggers] if isinstance(triggers, str) else (triggers or [])

    run = sd.get("run")
    repro = None
    if run:
        lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
        if env_names:
            lines.append("# env used by this job (fill in real values; secrets are NOT in the repo):")
            lines += [f"# export {n}=..." for n in env_names]
        if wd:
            lines.append(f"cd {wd}")
        lines.append(EXPR.sub(lambda m: f"<{m.group(1)}>", str(run)))
        repro = "\n".join(lines)

    strategy = jd.get("strategy") if isinstance(jd.get("strategy"), dict) else {}
    try:
        wf_file = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        wf_file = str(path)
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_file": wf_file,
        "workflow_name": data.get("name") or path.stem,
        "triggers": trigger_names,
        "job_id": job_id,
        "job_name": jd.get("name") or job_id,
        "runs_on": jd.get("runs-on"),
        "container": jd.get("container"),
        "matrix": strategy.get("matrix"),
        "timeout_minutes": jd.get("timeout-minutes"),
        "step_index": idx + 1,
        "step_name": step_display_name(sd),
        "step_uses": sd.get("uses"),
        "step_if": sd.get("if"),
        "continue_on_error": sd.get("continue-on-error"),
        "working_directory": wd,
        "shell": shell,
        "run": run,
        "repro_script": repro,
        "toolchain": toolchain,
        "secrets_referenced": secrets,
        "vars_referenced": variables,
        "env_names": env_names,
        "permissions": jd.get("permissions", data.get("permissions")),
        "match_confidence": confidence,
    }


def local_versions(toolchain: List[Dict[str, Any]], timeout: int = 15) -> List[Dict[str, Any]]:
    results = []
    for t in toolchain:
        cmd = t.get("local_cmd") or []
        if not cmd or not shutil.which(cmd[0]):
            results.append({"tool": t["tool"], "ci": t["pins"], "local": "not installed"})
            continue
        try:
            p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
            out = [ln.strip() for ln in (p.stdout + "\n" + p.stderr).splitlines() if ln.strip()]
            # Some tools print banners first (flutter); report the first line with a version number.
            version_line = next((ln for ln in out if VERSION_RX.search(ln)), out[0] if out else "?")
            results.append({"tool": t["tool"], "ci": t["pins"], "local": version_line[:120]})
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append({"tool": t["tool"], "ci": t["pins"], "local": f"error: {exc}"})
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Find the failing step in .github/workflows and print its command, toolchain and secrets.")
    ap.add_argument("--repo", default=".", help="repository root (default: .)")
    ap.add_argument("--workflow", help="workflow name as shown in GitHub (or file name)")
    ap.add_argument("--job", help="job name as shown in GitHub, e.g. 'test (ubuntu-latest, 18)'")
    ap.add_argument("--step", help="step name as shown in GitHub, e.g. 'Run npm ci'")
    ap.add_argument("--check-local", action="store_true", help="also run local toolchain --version commands")
    ap.add_argument("--dump", metavar="FILE", help="debug: print the parsed YAML of one workflow file")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    if args.dump:
        try:
            print(json.dumps(parse_yaml(Path(args.dump).read_text(encoding="utf-8")), indent=2))
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"error: --repo {args.repo!r} is not a directory", file=sys.stderr)
        return 2
    info = find_step(repo, args.workflow, args.job, args.step)
    if "error" in info:
        print(json.dumps(info, indent=2))
        return 1
    if args.check_local:
        info["local_versions"] = local_versions(info["toolchain"])
    for t in info["toolchain"]:
        t.pop("local_cmd", None)
    print(json.dumps(info, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
