"""End-to-end tests for ci_doctor.py in --log-file mode (no network, no gh)."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from _helpers import FIXTURES, SCRIPTS, secrets_fixture

WORKFLOW = textwrap.dedent("""\
    name: CI
    on: [push]
    jobs:
      build:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-node@v4
            with:
              node-version: 18
          - run: npm ci --loglevel=http
            env:
              CI: "true"
""")


class TestCiDoctor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        (self.repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
        (self.repo / "package.json").write_text('{"name": "demo"}')
        (self.repo / "package-lock.json").write_text("{}")
        self.out = Path(self.tmp.name) / "bundle"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def doctor(self, fixture: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "ci_doctor.py"), "--log-file", str(FIXTURES / fixture),
             "--repo-dir", str(self.repo), "--out-dir", str(self.out), "--no-local", *extra],
            capture_output=True, text=True)

    def test_brief_for_lockfile_failure(self) -> None:
        p = self.doctor("node_npm_ci_lockfile.log")
        self.assertEqual(p.returncode, 0, p.stderr)
        brief = p.stdout
        self.assertIn("# CI Doctor triage brief", brief)
        self.assertIn("NODE-01", brief)
        self.assertIn("**Category guess:** dependency", brief)
        self.assertIn("## Failing step in the workflow", brief)
        self.assertIn("npm ci --loglevel=http", brief)
        self.assertLess(len(brief.splitlines()), 260)
        for f in ("digest.md", "digest.json", "matches.json", "workflow.json", "repro.sh", "brief.md"):
            self.assertTrue((self.out / f).is_file(), f)

    def test_json_output(self) -> None:
        p = self.doctor("flaky_timeout_test.log", "--format", "json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["matches"][0]["id"], "FLAKY-01")
        self.assertIn("error mentions timing/network", data["flaky_signals"])

    def test_secrets_never_reach_brief_or_bundle(self) -> None:
        secrets = secrets_fixture()
        p = self.doctor("log_with_secrets.log")
        self.assertEqual(p.returncode, 0, p.stderr)
        texts = [p.stdout] + [f.read_text() for f in self.out.iterdir() if f.suffix in (".md", ".json")]
        for t in texts:
            for s in secrets:
                self.assertNotIn(s, t)

    def test_missing_log_file(self) -> None:
        p = subprocess.run([sys.executable, str(SCRIPTS / "ci_doctor.py"), "--log-file", "/no/such.log"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_fetch_failure_is_explained(self) -> None:
        empty = Path(self.tmp.name) / "empty-path"
        empty.mkdir()
        p = subprocess.run([sys.executable, str(SCRIPTS / "ci_doctor.py"), "--branch", "main",
                            "--out-dir", str(self.out)], capture_output=True, text=True,
                           env={"PATH": str(empty)})
        self.assertEqual(p.returncode, 2)
        self.assertIn("--log-file", p.stdout)

    def test_full_job_log_maps_step_without_job_name(self) -> None:
        (self.repo / ".github" / "workflows" / "android.yml").write_text(textwrap.dedent("""\
            name: Android
            on: [pull_request]
            jobs:
              build-android:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-java@v4
                    with: {distribution: temurin, java-version: "11"}
                  - run: flutter build apk --release
            """))
        p = self.doctor("flutter_gradle_jdk_mismatch.log", "--format", "json")
        self.assertEqual(p.returncode, 0, p.stderr)
        wf = json.loads(p.stdout)["workflow_step"]
        self.assertEqual(wf["workflow_file"], ".github/workflows/android.yml")
        self.assertEqual(wf["run"], "flutter build apk --release")
        self.assertEqual(wf["toolchain"][0]["tool"], "java")

    def test_json_contract_is_versioned(self) -> None:
        p = self.doctor("node_jest_failure.log", "--format", "json")
        data = json.loads(p.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["digest"]["schema_version"], 1)
        self.assertEqual(data["warnings"], [])

    def test_optional_phase_failure_degrades_to_warning(self) -> None:
        import ci_doctor
        import match_patterns

        args = ci_doctor.main.__globals__["argparse"].Namespace(
            log_file=str(FIXTURES / "node_npm_ci_lockfile.log"), run=None, pr=None, branch=None, repo=None,
            repo_dir=str(self.repo), out_dir=str(self.out), max_lines=120, context=15, top=5,
            no_local=True, discard_raw=False, format="markdown")
        buf = io.StringIO()
        with mock.patch.object(match_patterns, "load_library", side_effect=RuntimeError("boom")), \
                redirect_stdout(buf):
            code = ci_doctor.run(args)
        self.assertEqual(code, 0)
        brief = buf.getvalue()
        self.assertIn("**Warning:** pattern matching skipped (RuntimeError: boom)", brief)
        self.assertIn("## Failing step in the workflow", brief)  # other phases still ran

    def test_all_scripts_have_help_and_version(self) -> None:
        for script in sorted(SCRIPTS.glob("[!_]*.py")):
            with self.subTest(script=script.name):
                p = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("usage:", p.stdout)
                p = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertRegex(p.stdout, r"\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
