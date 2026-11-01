"""Tests for fetch_failed_logs.py against a fake `gh` executable (no network)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

from _helpers import SCRIPTS

FAKE_GH = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    args = sys.argv[1:]
    if "-R" in args:
        i = args.index("-R")
        with open(os.environ["FAKE_GH_CALLS"], "a") as fh:
            fh.write("repo=" + args[i + 1] + "\\n")
        args = args[:i] + args[i + 2:]
    cmd = " ".join(args)
    scenario = json.load(open(os.environ["FAKE_GH_SCENARIO"]))
    best = max((k for k in scenario if cmd.startswith(k)), key=len, default=None)
    if best is None:
        sys.stderr.write("fake gh: unexpected command: " + cmd + "\\n")
        sys.exit(99)
    r = scenario[best]
    if r.get("fail_first"):  # simulate transient failures before succeeding
        counter = os.environ["FAKE_GH_SCENARIO"] + "." + str(abs(hash(best)))
        n = int(open(counter).read()) if os.path.exists(counter) else 0
        open(counter, "w").write(str(n + 1))
        if n < r["fail_first"]:
            sys.stderr.write(r.get("fail_stderr", "HTTP 502: Bad Gateway"))
            sys.exit(1)
    out = r.get("stdout", "")
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
    sys.stderr.write(r.get("stderr", ""))
    sys.exit(r.get("code", 0))
""")

RUN_FAILED = {"databaseId": 2, "number": 12, "workflowName": "CI", "workflowDatabaseId": 77, "headBranch": "main",
              "headSha": "bbbbbbb2222", "status": "completed", "conclusion": "failure", "event": "push",
              "createdAt": "2026-10-14T10:00:00Z", "url": "https://github.com/o/r/actions/runs/2",
              "displayTitle": "Add zod", "attempt": 1}
RUN_OK = dict(RUN_FAILED, databaseId=1, number=11, headSha="aaaaaaa1111", conclusion="success",
              createdAt="2026-10-14T09:00:00Z", url="https://github.com/o/r/actions/runs/1")
RUN_RUNNING = dict(RUN_FAILED, databaseId=3, number=13, status="in_progress", conclusion="",
                   createdAt="2026-10-14T11:00:00Z")
VIEW = dict(RUN_FAILED, jobs=[
    {"databaseId": 901, "name": "build", "conclusion": "failure", "url": "https://github.com/o/r/actions/runs/2/job/901",
     "steps": [{"number": 1, "name": "Set up job", "conclusion": "success"},
               {"number": 4, "name": "Run npm ci", "conclusion": "failure"}]},
    {"databaseId": 902, "name": "lint", "conclusion": "success", "steps": []},
])
LOG = "build\tRun npm ci\t2026-10-14T10:01:00.0000000Z npm error code EUSAGE\n"


class FakeGhTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.scenario_path = Path(self.tmp.name) / "scenario.json"
        self.calls = Path(self.tmp.name) / "calls.txt"
        self.calls.write_text("")
        self.out_dir = Path(self.tmp.name) / "out"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def scenario(self, **overrides: dict) -> None:
        base = {
            "auth status": {"code": 0},
            "run list --branch main": {"stdout": [RUN_RUNNING, RUN_FAILED, RUN_OK]},
            "run view 2 --json": {"stdout": VIEW},
            "run view 2 --log-failed": {"stdout": LOG},
            "run list --workflow 77": {"stdout": [RUN_RUNNING, RUN_FAILED, RUN_OK]},
        }
        base.update(overrides)
        self.scenario_path.write_text(json.dumps(base))

    def run_fetch(self, *args: str, path: str = "") -> subprocess.CompletedProcess:
        env = dict(os.environ, PATH=path or f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
                   FAKE_GH_SCENARIO=str(self.scenario_path), FAKE_GH_CALLS=str(self.calls),
                   CI_EXPLAINER_RETRY_DELAY="0", PYTHONHASHSEED="0")
        return subprocess.run([sys.executable, str(SCRIPTS / "fetch_failed_logs.py"), *args,
                               "--out-dir", str(self.out_dir)], capture_output=True, text=True, env=env)


class TestFetch(FakeGhTest):
    def test_latest_failure_on_branch(self) -> None:
        self.scenario()
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["run"]["id"], 2)
        self.assertEqual(data["failed_jobs"][0]["name"], "build")
        self.assertEqual(data["failed_jobs"][0]["failed_steps"][0]["name"], "Run npm ci")
        self.assertEqual(data["last_success"]["head_sha"], "aaaaaaa1111")
        self.assertFalse(data["same_sha_succeeded"])
        self.assertEqual(data["history"], "✗✓")
        self.assertTrue(any("in_progress" in n for n in data["notes"]))
        log = Path(data["log_path"])
        self.assertEqual(log.read_text(), LOG)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertTrue(any("git diff --stat aaaaaaa1111..bbbbbbb2222" in n for n in data["next"]))

    def test_run_url_sets_repo(self) -> None:
        self.scenario(**{"run view --job 901 --log-failed": {"stdout": LOG}})
        p = self.run_fetch("--run", "https://github.com/o/r/actions/runs/2/job/901")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("repo=o/r", self.calls.read_text())
        self.assertEqual(json.loads(p.stdout)["repo"], "o/r")

    def test_same_sha_passed_is_flaky_signal(self) -> None:
        rerun_ok = dict(RUN_OK, headSha="bbbbbbb2222")
        self.scenario(**{"run list --workflow 77": {"stdout": [RUN_FAILED, rerun_ok]}})
        data = json.loads(self.run_fetch("--branch", "main").stdout)
        self.assertTrue(data["same_sha_succeeded"])
        self.assertTrue(any("SAME commit" in n for n in data["notes"]))

    def test_empty_log_failed_falls_back_to_job_log(self) -> None:
        self.scenario(**{"run view 2 --log-failed": {"stdout": ""},
                         "run view --job 901 --log": {"stdout": LOG}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(Path(json.loads(p.stdout)["log_path"]).read_text(), LOG)

    def test_transient_errors_are_retried(self) -> None:
        self.scenario(**{"run view 2 --json": {"stdout": VIEW, "fail_first": 2}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["run"]["id"], 2)

    def test_persistent_server_errors_give_up(self) -> None:
        self.scenario(**{"run view 2 --json": {"stdout": VIEW, "fail_first": 99}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 1)
        self.assertIn("502", p.stderr)
        self.assertEqual(json.loads(p.stdout)["exit_code"], 1)


class TestFetchErrors(FakeGhTest):
    def test_gh_not_installed(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        p = self.run_fetch("--branch", "main", path=str(empty))
        self.assertEqual(p.returncode, 2)
        self.assertIn("not installed", p.stderr)

    def test_not_authenticated(self) -> None:
        self.scenario(**{"auth status": {"code": 1, "stderr": "You are not logged into any GitHub hosts."}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 2)
        self.assertIn("gh auth login", p.stderr)

    def test_no_failed_runs(self) -> None:
        self.scenario(**{"run list --branch main": {"stdout": [RUN_OK]}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 4)
        self.assertIn("No failed runs", p.stderr)

    def test_only_running(self) -> None:
        self.scenario(**{"run list --branch main": {"stdout": [RUN_RUNNING, RUN_OK]}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 3)
        self.assertIn("in progress", p.stderr)

    def test_run_still_in_progress(self) -> None:
        self.scenario(**{"run view 3 --json": {"stdout": dict(VIEW, databaseId=3, status="in_progress")}})
        p = self.run_fetch("--run", "3")
        self.assertEqual(p.returncode, 3)
        self.assertIn("gh run watch 3", p.stderr)

    def test_logs_expired(self) -> None:
        self.scenario(**{"run view 2 --log-failed": {"code": 1, "stderr": "HTTP 410: Gone (log not found)"}})
        p = self.run_fetch("--branch", "main")
        self.assertEqual(p.returncode, 5)
        self.assertIn("no longer available", p.stderr)

    def test_bad_run_argument(self) -> None:
        self.scenario()
        p = self.run_fetch("--run", "not-a-run")
        self.assertEqual(p.returncode, 2)

    def test_help(self) -> None:
        p = subprocess.run([sys.executable, str(SCRIPTS / "fetch_failed_logs.py"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--pr", p.stdout)


if __name__ == "__main__":
    unittest.main()
