"""Tests for detect_stack.py using temporary project layouts."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _helpers import SCRIPTS
import detect_stack


def make_repo(files: dict) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    for rel, content in files.items():
        p = Path(tmp.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp


def stacks(result: dict) -> list:
    return [s["stack"] for s in result["stacks"]]


class TestDetectStack(unittest.TestCase):
    def test_flutter_project(self) -> None:
        with make_repo({
            "pubspec.yaml": "name: app\nenvironment:\n  sdk: '>=3.4.0 <4.0.0'\n  flutter: '3.24.5'\ndependencies:\n  flutter:\n    sdk: flutter\n",
            "android/build.gradle": "// gradle", "android/settings.gradle": "",
        }) as d:
            r = detect_stack.detect(Path(d))
            self.assertEqual(stacks(r)[0], "flutter")
            self.assertNotIn("jvm", stacks(r), "android/ gradle files belong to the flutter stack")
            self.assertIn("stack-flutter.md", r["references"])
            self.assertEqual(r["toolchain"]["flutter constraint"], "3.24.5")

    def test_node_project_with_lockfile_and_engines(self) -> None:
        with make_repo({
            "package.json": json.dumps({"name": "x", "engines": {"node": ">=20"}, "type": "module"}),
            "pnpm-lock.yaml": "", ".nvmrc": "20.15.0\n",
        }) as d:
            r = detect_stack.detect(Path(d))
            self.assertEqual(stacks(r), ["node"])
            self.assertEqual(r["toolchain"]["node_package_manager"], "pnpm")
            self.assertEqual(r["toolchain"][".nvmrc"], "20.15.0")
            self.assertEqual(r["toolchain"]["package.json engines"], {"node": ">=20"})

    def test_python_variants(self) -> None:
        for files in ({"pyproject.toml": '[project]\nrequires-python = ">=3.11"\n', "uv.lock": ""},
                      {"requirements.txt": "flask\n"}, {"setup.py": ""}, {"requirements-dev.txt": ""}):
            with self.subTest(files=list(files)), make_repo(files) as d:
                r = detect_stack.detect(Path(d))
                self.assertIn("python", stacks(r))
        with make_repo({"pyproject.toml": '[project]\nrequires-python = ">=3.11"\n', "uv.lock": ""}) as d:
            tc = detect_stack.detect(Path(d))["toolchain"]
            self.assertEqual(tc["requires-python"], ">=3.11")
            self.assertEqual(tc["python_tool"], "uv")

    def test_docker_plus_node_monorepo(self) -> None:
        with make_repo({"Dockerfile": "FROM node:20", "web/package.json": "{}",
                        "docker-compose.yml": "services: {}"}) as d:
            r = detect_stack.detect(Path(d))
            self.assertEqual(stacks(r)[0], "docker")
            self.assertIn("node", stacks(r))
            node = next(s for s in r["stacks"] if s["stack"] == "node")
            self.assertLess(node["confidence"], 0.9)  # nested => lower confidence

    def test_other_ecosystems(self) -> None:
        for marker, stack in (("go.mod", "go"), ("Cargo.toml", "rust"), ("pom.xml", "jvm"),
                              ("build.gradle.kts", "jvm"), ("Gemfile", "general")):
            with self.subTest(marker=marker), make_repo({marker: "module x\ngo 1.22\n"}) as d:
                self.assertIn(stack, stacks(detect_stack.detect(Path(d))))

    def test_vendored_dirs_ignored(self) -> None:
        with make_repo({"node_modules/foo/package.json": "{}", "src/main.go": "", "go.mod": "module x"}) as d:
            self.assertEqual(stacks(detect_stack.detect(Path(d))), ["go"])

    def test_log_signals_only(self) -> None:
        with make_repo({}) as d:
            r = detect_stack.detect(Path(d), "Run flutter pub get\nlib/main.dart:3:1: Error")
            self.assertEqual(stacks(r), ["flutter"])
            self.assertLess(r["stacks"][0]["confidence"], 0.9)

    def test_empty_repo_falls_back_to_general(self) -> None:
        with make_repo({}) as d:
            r = detect_stack.detect(Path(d))
            self.assertEqual(r["stacks"], [])
            self.assertEqual(r["references"], ["stack-general.md"])

    def test_cli(self) -> None:
        with make_repo({"Cargo.toml": ""}) as d:
            out = subprocess.run([sys.executable, str(SCRIPTS / "detect_stack.py"), "--repo", d],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["stacks"][0]["stack"], "rust")
        out = subprocess.run([sys.executable, str(SCRIPTS / "detect_stack.py"), "--repo", "/no/such/dir"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
