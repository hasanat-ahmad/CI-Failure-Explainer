"""Tests for clean_logs.py: budget, root cause, redaction, normalisation, robustness."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

from _helpers import FIXTURES, SCRIPTS, expected, fixture_text, secrets_fixture
import clean_logs

ANSI = re.compile(r"\x1b\[")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def digest_md(raw: str, **kw) -> str:
    return clean_logs.render_markdown(clean_logs.digest_text(raw, "test.log", **kw))


class TestFixtures(unittest.TestCase):
    def test_every_fixture_within_budget_and_keeps_root_cause(self) -> None:
        for name, exp in expected().items():
            with self.subTest(fixture=name):
                md = digest_md(fixture_text(name))
                self.assertLessEqual(len(md.splitlines()), 150)
                for needle in exp["must_contain"]:
                    self.assertIn(needle, md)

    def test_tight_budget_still_respected(self) -> None:
        for name in expected():
            with self.subTest(fixture=name):
                md = digest_md(fixture_text(name), max_lines=40, context=10)
                self.assertLessEqual(len(md.splitlines()), 40)

    def test_digest_is_much_smaller_than_raw(self) -> None:
        for name in expected():
            raw = fixture_text(name)
            md = digest_md(raw)
            self.assertLess(len(md.splitlines()), len(raw.splitlines()) / 3, name)

    def test_ansi_and_timestamps_removed(self) -> None:
        for name in expected():
            md = digest_md(fixture_text(name))
            self.assertIsNone(ANSI.search(md), name)
            self.assertIsNone(TIMESTAMP.search(md), name)

    def test_root_cause_marker_and_header(self) -> None:
        md = digest_md(fixture_text("python_pytest_failure.log"))
        self.assertIn("**Root-cause candidate:** `E   ModuleNotFoundError: No module named 'httpx'`", md)
        self.assertRegex(md, r"▶\s+\d+ \| E   ModuleNotFoundError")
        self.assertIn("**Exit code:** 2", md)

    def test_dart_test_failure_header_is_root_cause(self) -> None:
        raw = "\n".join([
            "00:00 +12: test/a_test.dart: passes",
            "  web 1.1.1 (1.1.2 available)",
            "00:00 +13 -1: test/format_test.dart: month keys are zero padded [E]",
            "  Expected: '2026-09'",
            "    Actual: '2026-009'",
            "00:01 +40 -1: Some tests failed.",
        ])
        d = clean_logs.digest_text(raw)
        self.assertTrue(d.item(d.root_cause).text.endswith("[E]"))
        self.assertIn("Actual: '2026-009'", clean_logs.render_markdown(d))

    def test_gradle_cause_chain_followed(self) -> None:
        d = clean_logs.digest_text(fixture_text("flutter_gradle_jdk_mismatch.log"))
        self.assertIn("Android Gradle plugin requires Java 17", d.item(d.root_cause).text)


class TestRedaction(unittest.TestCase):
    def setUp(self) -> None:
        self.secrets = secrets_fixture()
        self.raw = fixture_text("log_with_secrets.log")
        self.assertGreaterEqual(len(self.secrets), 15)

    def assertNoSecrets(self, text: str) -> None:
        for s in self.secrets:
            self.assertNotIn(s, text)
            self.assertNotIn(s[:16], text, "a recognisable prefix of a secret leaked")

    def test_markdown_digest_has_no_secrets(self) -> None:
        self.assertNoSecrets(digest_md(self.raw))

    def test_json_digest_has_no_secrets(self) -> None:
        d = clean_logs.digest_text(self.raw)
        out = clean_logs.render_json(d)
        self.assertNoSecrets(out)
        data = json.loads(out)
        self.assertGreaterEqual(data["secrets_redacted"], 15)

    def test_slice_and_grep_have_no_secrets(self) -> None:
        for args in (["--slice", "150:186"], ["--grep", "."]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                clean_logs.main([str(FIXTURES / "log_with_secrets.log"), *args])
            self.assertNoSecrets(buf.getvalue())

    def test_counts_only_never_values(self) -> None:
        md = digest_md(self.raw)
        self.assertRegex(md, r"\*\*Secrets redacted:\*\* \d+ \(")
        self.assertIn("[REDACTED PRIVATE KEY]", md)

    def test_injection_lines_flagged(self) -> None:
        md = digest_md(self.raw)
        self.assertIn("[!injection?]", md)
        self.assertIn("look like prompt-injection", md)

    def test_individual_rules(self) -> None:
        cases = {
            "token=ghp_" + "a" * 36: "ghp_",
            "Authorization: Bearer abcdefghijklmnop1234": "abcdefghijklmnop1234",
            "url https://user:hunter2hunter2@example.com/x": "hunter2hunter2",
            "AKIA" + "ABCDEFGHIJKLMNOP is the id": "AKIA" + "ABCDEFGHIJKLMNOP",
            "password: correcthorsebattery": "correcthorsebattery",
            "-----BEGIN RSA " + "PRIVATE KEY-----abc-----END RSA " + "PRIVATE KEY-----": "abc",
            "remote: glpat-" + "x1Y2z3" * 4 + " rejected": "x1Y2z3" * 4,
            "HF_HOME set, login hf_" + "AbCd" * 9: "AbCd" * 9,
            "docker login -p dckr_pat_" + "Zz9" * 9: "Zz9" * 9,
            "sendgrid SG." + "a" * 22 + "." + "b" * 43: "b" * 43,
            "doctl auth dop_v1_" + "c0ffee" * 10 + "c0ff": "c0ffee" * 10,
            "shop shpat_" + "d" * 32: "d" * 32,
            "DefaultEndpointsProtocol=https;AccountName=acme;AccountKey=" + "Qw/+" * 10 + "==;": "Qw/+" * 10,
            "GET https://acme.blob.core.windows.net/c/x?sv=2024-01-01&sig=" + "Ab%2B" * 8: "Ab%2B" * 8,
        }
        for line, secret in cases.items():
            with self.subTest(line=line):
                r = clean_logs.Redactor()
                out = r.redact(line) or ""
                self.assertNotIn(secret, out.replace("[REDACTED", ""))
                self.assertGreater(r.total, 0)

    def test_safe_values_untouched(self) -> None:
        for line in ("token: ${{ secrets.NPM_TOKEN }}", "max_tokens=1024", "TokenError: invalid grant",
                     "src/auth.ts:12:5 - error TS2322", "password: required", "GITHUB_TOKEN: ***",
                     "from huggingface_hub import hf_hub_download", "SG.Utils.format(x)",
                     "AccountName=acme;AccountKey=***"):
            with self.subTest(line=line):
                r = clean_logs.Redactor()
                self.assertEqual(r.redact(line), line)
                self.assertEqual(r.total, 0)


class TestNormalisation(unittest.TestCase):
    def test_gh_prefix_parsed_into_job_and_step(self) -> None:
        raw = "build\tRun make\t2026-01-01T00:00:00.0000000Z make: *** [all] Error 2\n" \
              "build\tRun make\t2026-01-01T00:00:01.0000000Z ##[error]Process completed with exit code 2."
        d = clean_logs.digest_text(raw)
        self.assertEqual([(s.job, s.step) for s in d.steps], [("build", "Run make")])
        self.assertEqual(d.exit_code, 2)

    def test_duplicates_collapsed(self) -> None:
        raw = "\n".join(["warning: retrying connection 1"] * 500 + ["fatal: could not connect", "Error: exit code 1"])
        md = digest_md(raw)
        self.assertIn("similar lines", md)
        self.assertLess(len(md.splitlines()), 30)

    def test_long_stack_traces_folded(self) -> None:
        names = ["render", "commit", "flush", "dispatch", "reduce", "invoke", "apply", "resolve", "handle", "emit"]
        frames = [f"    at {names[i % 10]}{chr(65 + i % 26)}x (/app/src/{names[(i * 3) % 10]}.js:{i}:1)" for i in range(60)]
        raw = "\n".join(["TypeError: Cannot read properties of undefined (reading 'id')", *frames, "exit code 1"])
        md = digest_md(raw)
        self.assertIn("similar stack frames omitted", md)

    def test_progress_bars_and_carriage_returns(self) -> None:
        raw = "Downloading [#####-----] 50%\rDownloading [##########] 100%\nERROR: build failed\nexit code 1"
        md = digest_md(raw)
        self.assertNotIn("50%", md)
        self.assertIn("ERROR: build failed", md)

    def test_gitlab_sections_become_steps(self) -> None:
        esc = "\x1b[0K"
        raw = "\n".join([
            f"{esc}section_start:1730000000:prepare_executor\r{esc}Preparing the \"docker\" executor",
            "Using docker image sha256:abc for php:8.3-cli ...",
            f"section_end:1730000001:prepare_executor\r{esc}",
            f"{esc}section_start:1730000002:step_script\r{esc}Executing \"step_script\" stage of the job script",
            "\x1b[32;1m$ composer install --no-interaction\x1b[0;m",
            f"{esc}section_start:1730000003:install[collapsed=true]\r{esc}Install dependencies",
            "fatal: unable to access 'https://gitlab.example.com/acme/lib.git/': The requested URL returned error: 403",
            f"section_end:1730000004:install\r{esc}",
            f"section_end:1730000005:step_script\r{esc}",
            f"{esc}section_start:1730000006:cleanup_file_variables\r{esc}Cleaning up project directory",
            f"section_end:1730000007:cleanup_file_variables\r{esc}",
            "\x1b[31;1mERROR: Job failed: exit code 2\x1b[0;m",
        ])
        d = clean_logs.digest_text(raw)
        self.assertEqual([s.step for s in d.steps], ["prepare_executor", "step_script", "install", "cleanup_file_variables"])
        self.assertEqual([s.step for s in d.steps if s.failed], ["install"])   # trailer credited to the script
        self.assertIn("unable to access", d.item(d.root_cause).text)
        self.assertEqual(d.exit_code, 2)
        md = clean_logs.render_markdown(d)
        self.assertNotIn("section_", md)
        self.assertIn("▸ Install dependencies", md)

    def test_jenkins_stages_become_steps(self) -> None:
        raw = "\n".join([
            "Started by user ci-admin",
            "[Pipeline] Start of Pipeline", "[Pipeline] node", "Running on agent-2 in /var/jenkins/workspace/api",
            "[Pipeline] {", "[Pipeline] stage", "[Pipeline] { (Build)", "[Pipeline] sh", "+ go build ./...",
            "[Pipeline] }", "[Pipeline] // stage", "[Pipeline] stage", "[Pipeline] { (Test)", "[Pipeline] sh",
            "[2026-10-14T09:12:03.120Z] + go test ./...",
            "[2026-10-14T09:12:09.551Z] --- FAIL: TestRates (0.01s)",
            "    rates_test.go:31: got 0.89, want 0.9",
            "FAIL\tgithub.com/acme/api/rates\t0.412s",
            "[Pipeline] }", "[Pipeline] // stage", "[Pipeline] stage", "[Pipeline] { (Deploy)",
            'Stage "Deploy" skipped due to earlier failure(s)', "[Pipeline] }", "[Pipeline] // stage",
            "[Pipeline] stage", "[Pipeline] { (Declarative: Post Actions)", "[Pipeline] junit",
            "Recording test results", "[Pipeline] }", "[Pipeline] // stage",
            "[Pipeline] }", "[Pipeline] // node", "[Pipeline] End of Pipeline",
            "ERROR: script returned exit code 1", "Finished: FAILURE",
        ])
        d = clean_logs.digest_text(raw)
        self.assertEqual([s.step for s in d.steps], ["log", "Build", "Test", "Deploy", "Declarative: Post Actions"])
        self.assertEqual([s.step for s in d.steps if s.failed], ["Test"])
        self.assertEqual(d.item(d.root_cause).text, "--- FAIL: TestRates (0.01s)")
        md = clean_logs.render_markdown(d)
        self.assertNotIn("[Pipeline]", md)
        self.assertNotIn("2026-10-14T", md)

    def test_carriage_returns_do_not_shift_line_numbers(self) -> None:
        raw = "step 1\nprogress 10%\rprogress 100%\nfatal: here\n"
        lines = clean_logs.redacted_lines(raw)
        self.assertEqual([(ln.n, ln.text) for ln in lines], [(1, "step 1"), (2, "progress 100%"), (3, "fatal: here")])
        self.assertEqual(clean_logs.digest_text(raw).raw_lines, 3)

    def test_gitlab_timestamped_stream_prefix_stripped(self) -> None:
        raw = "2026-05-22T12:43:46.962646Z 00O $ make test\n2026-05-22T12:43:47.000001Z 01E fatal: boom"
        lines = clean_logs.redacted_lines(raw)
        self.assertEqual([ln.text for ln in lines], ["$ make test", "fatal: boom"])

    def test_annotations_understood(self) -> None:
        raw = "::error file=src/app.py,line=10,col=4::Undefined name 'foo'\n##[warning]Node 16 is deprecated"
        md = digest_md(raw)
        self.assertIn("[error] src/app.py:10:4: Undefined name 'foo'", md)


class TestRobustness(unittest.TestCase):
    def test_empty_input(self) -> None:
        md = digest_md("")
        self.assertIn("The log is empty", md)

    def test_binaryish_input(self) -> None:
        raw = bytes(range(256)).decode("latin-1") * 50 + "\nERROR: boom\n"
        md = digest_md(raw)
        self.assertIn("ERROR: boom", md)
        self.assertLessEqual(len(md.splitlines()), 150)

    def test_very_long_line_truncated(self) -> None:
        md = digest_md("Error: " + "x" * 10000)
        self.assertIn("chars)", md)

    def test_cli_help_and_stdin(self) -> None:
        script = str(SCRIPTS / "clean_logs.py")
        out = subprocess.run([sys.executable, script, "--help"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("--max-lines", out.stdout)
        out = subprocess.run([sys.executable, script, "-", "--format", "json"], input="fatal: nope\n",
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["root_cause"]["text"], "fatal: nope")

    def test_cli_missing_file(self) -> None:
        out = subprocess.run([sys.executable, str(SCRIPTS / "clean_logs.py"), "/no/such/file.log"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("cannot read log", out.stderr)


class TestProductionLimits(unittest.TestCase):
    """Hostile or huge inputs must stay bounded in time, size and secrecy."""

    def test_huge_log_with_many_distinct_errors_is_fast_and_bounded(self) -> None:
        lines = []
        for i in range(200_000):
            if i % 50 == 0:
                lines.append(f"src/mod{i}.ts({i % 97},{i % 13}): error TS{2000 + i % 900}: Type 'x{i}' is not assignable")
            else:
                lines.append(f"[build] compiling chunk {i} of 200000 ({i * 31 % 997} kB)")
        lines.append("##[error]Process completed with exit code 2.")
        start = time.perf_counter()
        md = digest_md("\n".join(lines))
        elapsed = time.perf_counter() - start
        self.assertLessEqual(len(md.splitlines()), 150)
        self.assertIn("error TS2000", md)            # first specific error kept
        self.assertIn("exit code 2", md)
        self.assertLess(elapsed, 60, f"digest took {elapsed:.1f}s")

    def test_megabyte_line_redacted_then_truncated(self) -> None:
        secret = "ghp_" + "Q" * 36
        raw = "Error: " + "a " * 500_000 + secret + "\nexit code 1"
        md = digest_md(raw)
        self.assertNotIn(secret, md)
        self.assertNotIn("Q" * 20, md)
        self.assertLessEqual(len(md.splitlines()), 150)

    def test_oversized_input_keeps_head_and_tail(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile("wb", suffix=".log", delete=False) as fh:
            fh.write(b"FIRST LINE\n" + b"noise line\n" * 200_000 + b"fatal: the real error\n")
            path = fh.name
        try:
            text = clean_logs.read_input(path, max_bytes=100_000)
        finally:
            Path(path).unlink()
        self.assertTrue(text.startswith("FIRST LINE"))
        self.assertIn("bytes skipped from the middle", text)
        self.assertTrue(text.rstrip().endswith("fatal: the real error"))
        self.assertLess(len(text), 110_000)

    def test_json_is_versioned(self) -> None:
        data = json.loads(clean_logs.render_json(clean_logs.digest_text("fatal: x")))
        self.assertEqual(data["schema_version"], clean_logs.SCHEMA_VERSION)
        self.assertRegex(data["tool_version"], r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
