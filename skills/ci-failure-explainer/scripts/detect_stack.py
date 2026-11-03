#!/usr/bin/env python3
"""Identify the project's stack(s) from repository files and (optionally) the log.

Output is JSON: each detected stack with a confidence, the evidence behind it,
the matching reference file, plus toolchain hints (package manager, pinned
runtime versions) that help spot "works locally, fails in CI" drift.

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Sequence

from _version import __version__

SCHEMA_VERSION = 1

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "build", "dist", "target", "vendor",
    ".dart_tool", ".gradle", ".idea", "Pods", "__pycache__", ".next", ".tox", ".mypy_cache",
}
MAX_DEPTH = 3

# stack -> reference file under references/
REFERENCES = {
    "flutter": "stack-flutter.md",
    "node": "stack-node.md",
    "python": "stack-python.md",
    "docker": "stack-docker.md",
    "go": "stack-go.md",
    "rust": "stack-rust.md",
    "jvm": "stack-jvm.md",
    "ruby": "stack-ruby.md",
    "dotnet": "stack-dotnet.md",
    "general": "stack-general.md",
}

# (glob, stack, label). Labels name the ecosystem for "general" stacks.
FILE_MARKERS = [
    ("pubspec.yaml", "flutter", "pubspec.yaml"),
    ("package.json", "node", "package.json"),
    ("pyproject.toml", "python", "pyproject.toml"),
    ("requirements*.txt", "python", "requirements.txt"),
    ("setup.py", "python", "setup.py"),
    ("setup.cfg", "python", "setup.cfg"),
    ("Pipfile", "python", "Pipfile"),
    ("Dockerfile", "docker", "Dockerfile"),
    ("Dockerfile.*", "docker", "Dockerfile"),
    ("*.Dockerfile", "docker", "Dockerfile"),
    ("docker-compose*.yml", "docker", "docker-compose"),
    ("docker-compose*.yaml", "docker", "docker-compose"),
    ("compose.yaml", "docker", "compose.yaml"),
    ("go.mod", "go", "go.mod"),
    ("Cargo.toml", "rust", "Cargo.toml"),
    ("pom.xml", "jvm", "maven"),
    ("build.gradle", "jvm", "gradle"),
    ("build.gradle.kts", "jvm", "gradle"),
    ("settings.gradle*", "jvm", "gradle"),
    ("Gemfile", "ruby", "bundler"),
    ("*.gemspec", "ruby", "gemspec"),
    ("*.csproj", "dotnet", "csproj"),
    ("*.fsproj", "dotnet", "fsproj"),
    ("*.sln", "dotnet", "sln"),
    ("*.slnx", "dotnet", "sln"),
    ("global.json", "dotnet", "global.json"),
    ("composer.json", "general", "php"),
    ("Package.swift", "general", "swift"),
    ("mix.exs", "general", "elixir"),
    ("CMakeLists.txt", "general", "cmake"),
    ("Makefile", "general", "make"),
]

LOG_SIGNALS: Dict[str, List[str]] = {
    "flutter": [r"\bflutter\b", r"\bdart (?:analyze|format|test|pub)\b", r"subosito/flutter-action",
                r"pub get", r"\bpod install\b", r"\.dart:\d+:\d+"],
    "node": [r"npm (?:ERR!|error|ci|install|run)", r"\byarn (?:install|run|build)\b", r"\bpnpm\b",
             r"node_modules", r"\bjest\b", r"\bvitest\b", r"\btsc\b", r"\beslint\b", r"\bnext build\b"],
    "python": [r"\bpytest\b", r"pip install", r"Traceback \(most recent call last\)", r"\.py:\d+",
               r"\bpoetry\b", r"\bruff\b", r"\bmypy\b", r"\buv (?:sync|pip|run)\b", r"\btox\b"],
    "docker": [r"docker (?:build|push|compose)", r"\bbuildx\b", r"failed to solve", r"^#\d+ \[",
               r"Dockerfile", r"docker/build-push-action"],
    "go": [r"\bgo (?:build|test|vet|mod)\b", r"\.go:\d+:\d+", r"^--- FAIL:", r"golangci-lint"],
    "rust": [r"\bcargo (?:build|test|clippy|fmt)\b", r"\brustc\b", r"error\[E\d{4}\]"],
    "jvm": [r"\bgradle\b", r"\bmvn\b", r"\bmaven\b", r"\.java:\d+", r"\.kt:\d+", r"\bkotlin\b"],
    "ruby": [r"\bbundle (?:install|exec|lock)\b", r"\brspec\b", r"\brubocop\b", r"\brails (?:test|db:|assets:)",
             r"ruby/setup-ruby", r"\.rb:\d+", r"\bBundler::", r"Gemfile\.lock"],
    "dotnet": [r"\bdotnet (?:build|test|restore|publish|format)\b", r"(?i)\bmsbuild\b", r"\berror (?:NU|MSB|NETSDK|CS)\d{4}\b",
               r"\.[cf]sproj\b", r"actions/setup-dotnet"],
}


def _iter_files(root: Path, pattern: str) -> List[Path]:
    """Find files matching pattern up to MAX_DEPTH, skipping vendored/build dirs."""
    found: List[Path] = []

    def walk(d: Path, depth: int) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_file() and e.match(pattern):
                found.append(e)
        if depth < MAX_DEPTH:
            for e in entries:
                if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith("."):
                    walk(e, depth + 1)

    walk(root, 0)
    return found


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def toolchain_hints(root: Path, project_dirs: Sequence[str] = ()) -> Dict[str, object]:
    """Pinned versions and package managers for the root and each sub-project (monorepos).

    Keys from sub-projects are prefixed with their directory, e.g. "functions/: package.json engines".
    """
    hints: Dict[str, object] = dict(_hints_for(root))
    for rel in sorted(set(project_dirs) - {""}):
        for key, value in _hints_for(root / rel).items():
            hints[f"{rel}/: {key}"] = value
    return hints


def _hints_for(root: Path) -> Dict[str, object]:
    hints: Dict[str, object] = {}
    for name in (".nvmrc", ".node-version", ".python-version", ".ruby-version", ".java-version",
                 ".go-version", ".fvmrc", "rust-toolchain", "rust-toolchain.toml", ".tool-versions"):
        p = root / name
        if p.is_file():
            hints[name] = _read(p, 500).strip()
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(_read(pkg))
            if isinstance(data.get("engines"), dict):
                hints["package.json engines"] = data["engines"]
            if data.get("packageManager"):
                hints["packageManager"] = data["packageManager"]
            if data.get("type"):
                hints["package.json type"] = data["type"]
        except ValueError:
            hints["package.json"] = "invalid JSON"
    for lock, pm in (("package-lock.json", "npm"), ("yarn.lock", "yarn"), ("pnpm-lock.yaml", "pnpm"),
                     ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if (root / lock).is_file():
            hints.setdefault("node_package_manager", pm)
    if pkg.is_file() and "node_package_manager" not in hints:
        hints["node_package_manager"] = "none (no lockfile committed)"
    for lock, tool in (("poetry.lock", "poetry"), ("uv.lock", "uv"), ("Pipfile.lock", "pipenv"),
                       ("pdm.lock", "pdm")):
        if (root / lock).is_file():
            hints.setdefault("python_tool", tool)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'requires-python\s*=\s*["\']([^"\']+)', _read(pyproject))
        if m:
            hints["requires-python"] = m.group(1)
    pubspec = root / "pubspec.yaml"
    if pubspec.is_file():
        env = re.search(r"^environment:[ \t]*\n((?:[ \t]+.*\n?)+)", _read(pubspec), re.M)  # indented block only
        block = env.group(1) if env else ""
        m = re.search(r"^[ \t]+sdk:[ \t]*['\"]?([^'\"\n]+)", block, re.M)
        if m:
            hints["dart sdk constraint"] = m.group(1).strip()
        m = re.search(r"^[ \t]+flutter:[ \t]*['\"]?([^'\"\n]+)", block, re.M)
        if m:
            hints["flutter constraint"] = m.group(1).strip()
    gemlock = root / "Gemfile.lock"
    if gemlock.is_file():
        text = _read(gemlock)
        m = re.search(r"^BUNDLED WITH\s*\n\s+(\S+)", text, re.M)
        if m:
            hints["bundler"] = m.group(1)
        m = re.search(r"^PLATFORMS\s*\n((?:[ \t]+\S+\n?)+)", text, re.M)
        if m:
            hints["Gemfile.lock platforms"] = m.group(1).split()
    gemfile = root / "Gemfile"
    if gemfile.is_file():
        m = re.search(r"^ruby\s+['\"]([^'\"]+)['\"]|^ruby\s+file:\s*['\"]([^'\"]+)", _read(gemfile), re.M)
        if m:
            hints["Gemfile ruby"] = m.group(1) or f"file: {m.group(2)}"
    global_json = root / "global.json"
    if global_json.is_file():
        try:
            sdk = json.loads(_read(global_json)).get("sdk") or {}
            if isinstance(sdk, dict) and sdk.get("version"):
                hints["global.json sdk"] = sdk["version"] + (f" (rollForward: {sdk['rollForward']})"
                                                             if sdk.get("rollForward") else "")
        except (ValueError, AttributeError):
            hints["global.json"] = "invalid JSON"
    for proj in sorted([*root.glob("*.csproj"), *root.glob("*.fsproj"), *root.glob("Directory.Build.props")])[:3]:
        text = _read(proj)
        for tag, key in (("TargetFrameworks?", "target framework"), ("TreatWarningsAsErrors", "TreatWarningsAsErrors"),
                         ("RestorePackagesWithLockFile", "RestorePackagesWithLockFile")):
            m = re.search(rf"<{tag}>\s*([^<]+?)\s*</", text)
            if m:
                hints[f"{proj.name} {key}"] = m.group(1)
    gomod = root / "go.mod"
    if gomod.is_file():
        m = re.search(r"^go\s+(\S+)", _read(gomod), re.M)
        if m:
            hints["go.mod go"] = m.group(1)
    return hints


def detect(root: Path, log_text: Optional[str] = None) -> Dict[str, object]:
    stacks: Dict[str, Dict[str, object]] = {}

    def add(stack: str, conf: float, evidence: str, label: Optional[str] = None) -> None:
        entry = stacks.setdefault(stack, {"stack": stack, "confidence": 0.0, "evidence": [], "labels": []})
        entry["confidence"] = max(float(entry["confidence"]), conf)  # type: ignore[arg-type]
        if evidence not in entry["evidence"]:  # type: ignore[operator]
            entry["evidence"].append(evidence)  # type: ignore[union-attr]
        if label and label not in entry["labels"]:  # type: ignore[operator]
            entry["labels"].append(label)  # type: ignore[union-attr]

    project_dirs: List[str] = []   # directories holding a manifest (monorepo sub-projects)
    flutter_dirs: List[str] = []
    if root.is_dir():
        for pattern, stack, label in FILE_MARKERS:
            for f in _iter_files(root, pattern)[:5]:
                rel = f.relative_to(root).as_posix()
                depth = rel.count("/")
                conf = 0.9 if depth == 0 else (0.7 if depth == 1 else 0.5)
                file_label = label
                if stack == "flutter" and not re.search(r"^\s+flutter:\s*$|sdk:\s*flutter", _read(f), re.M):
                    file_label = "dart"  # pure Dart package: same reference applies
                if stack == "general" and label == "make":
                    conf = min(conf, 0.3)  # a Makefile alone says little
                add(stack, conf, f"file: {rel}", file_label)
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                if stack != "general" and parent not in project_dirs and depth <= 2:
                    project_dirs.append(parent)
                if stack == "flutter" and file_label != "dart":
                    flutter_dirs.append(parent)

    if log_text:
        for stack, patterns in LOG_SIGNALS.items():
            hits = [p for p in patterns if re.search(p, log_text, re.M)]
            if hits:
                conf = min(0.4 + 0.1 * len(hits), 0.8)
                if stack in stacks:
                    stacks[stack]["confidence"] = min(float(stacks[stack]["confidence"]) + 0.05, 0.99)  # type: ignore[arg-type]
                add(stack, conf, "log: " + ", ".join(hits[:3]))

    # Flutter apps contain Gradle files under <app>/android/ and their logs mention Gradle;
    # that is the Flutter stack, not a separate JVM project. Drop JVM unless other evidence exists.
    if "flutter" in stacks and "jvm" in stacks:
        android_dirs = tuple(f"file: {d + '/' if d else ''}android/" for d in flutter_dirs) or ("file: android/",)
        own = [e for e in stacks["jvm"]["evidence"]  # type: ignore[union-attr]
               if not str(e).startswith(android_dirs) and not str(e).startswith("log: ")]
        if not own:
            stacks["flutter"]["evidence"].append("android/ Gradle files (covered by stack-flutter.md)")  # type: ignore[union-attr]
            del stacks["jvm"]
            project_dirs = [d for d in project_dirs if not any(d.startswith(f"{f}/android" if f else "android")
                                                                for f in flutter_dirs)]

    ordered = sorted(stacks.values(), key=lambda s: -float(s["confidence"]))  # type: ignore[arg-type]
    for s in ordered:
        s["confidence"] = round(float(s["confidence"]), 2)  # type: ignore[arg-type]
        s["reference"] = REFERENCES[str(s["stack"])]
        if not s["labels"]:
            del s["labels"]

    refs = [str(s["reference"]) for s in ordered if float(s["confidence"]) >= 0.4]  # type: ignore[arg-type]
    if "stack-general.md" not in refs:
        refs.append("stack-general.md")  # infra/config failures apply to every stack
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root.resolve()) if root.exists() else str(root),
        "stacks": ordered,
        "references": refs,
        "toolchain": toolchain_hints(root, project_dirs) if root.is_dir() else {},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Detect project stack(s) from repo files and an optional log/digest.")
    ap.add_argument("--repo", default=".", help="repository root to scan (default: current directory)")
    ap.add_argument("--log", help="optional digest or log file to scan for stack-specific strings")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    root = Path(args.repo)
    if not root.is_dir():
        print(f"error: --repo {args.repo!r} is not a directory", file=sys.stderr)
        return 2
    log_text = None
    if args.log:
        try:
            log_text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"error: cannot read --log: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(detect(root, log_text), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
