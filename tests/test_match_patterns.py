"""Tests for match_patterns.py and the integrity of the reference library."""

from __future__ import annotations

import subprocess
import sys
import unittest

from _helpers import SCRIPTS, SKILL, expected, fixture_text
import clean_logs
import match_patterns

MIN_ENTRIES = {"stack-flutter.md": 10, "stack-node.md": 10, "stack-python.md": 10, "stack-docker.md": 10,
               "stack-general.md": 10, "stack-go.md": 10, "stack-rust.md": 10, "stack-jvm.md": 10,
               "stack-ruby.md": 10, "flaky-tests.md": 5}

# One real-looking log excerpt per entry family -> the entry that must rank first.
REPRESENTATIVE = {
    "RUBY-01": ("ruby", "You are trying to install in deployment mode after changing your Gemfile. Run `bundle install` "
                        "elsewhere and add the updated Gemfile.lock to version control."),
    "RUBY-02": ("ruby", "Your bundle only supports platforms [\"arm64-darwin-23\"] but your local platform is x86_64-linux."),
    "RUBY-03": ("ruby", "Your Ruby version is 3.2.4, but your Gemfile specified ~> 3.3.0"),
    "RUBY-05": ("ruby", "ERROR:  Failed to build gem native extension.\n"
                        "An error occurred while installing pg (1.5.9), and Bundler cannot continue."),
    "RUBY-07": ("ruby", "Failure:\nUserTest#test_rejects_blank_email [test/models/user_test.rb:8]:\n"
                        "Expected true to be nil or false\n12 runs, 30 assertions, 1 failures, 0 errors, 0 skips"),
    "RUBY-08": ("ruby", "app/models/user.rb:3:5: C: [Correctable] Style/StringLiterals: Prefer single-quoted strings\n"
                        "48 files inspected, 1 offense detected, 1 offense autocorrectable"),
    "RUBY-09": ("ruby", "ActiveRecord::ConnectionNotEstablished: connection to server at \"127.0.0.1\", port 5432 failed: "
                        "Connection refused"),
}


class TestLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = match_patterns.load_library()

    def test_library_is_valid(self) -> None:
        problems = [(e.id, err) for e in self.entries for err in e.errors]
        self.assertEqual(problems, [])
        ids = [e.id for e in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate entry ids")

    def test_minimum_entries_per_reference(self) -> None:
        for file, minimum in MIN_ENTRIES.items():
            with self.subTest(file=file):
                count = sum(1 for e in self.entries if e.file == file)
                self.assertGreaterEqual(count, minimum)

    def test_every_entry_has_standard_fields(self) -> None:
        for e in self.entries:
            for field in match_patterns.REQUIRED_FIELDS:
                self.assertTrue(e.fields.get(field, "").strip(), f"{e.id} missing {field}")

    def test_validate_cli(self) -> None:
        out = subprocess.run([sys.executable, str(SCRIPTS / "match_patterns.py"), "--validate"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class TestMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = match_patterns.load_library()

    def test_fixtures_top_match(self) -> None:
        for name, exp in expected().items():
            if "pattern" not in exp:
                continue
            with self.subTest(fixture=name):
                md = clean_logs.render_markdown(clean_logs.digest_text(fixture_text(name), name))
                matches = match_patterns.match(md, self.entries, [exp["stack"]])
                self.assertTrue(matches, "no match at all")
                self.assertEqual(matches[0].entry.id, exp["pattern"])
                self.assertEqual(matches[0].entry.category, exp["category"])

    def test_raw_log_input_is_redacted_before_matching(self) -> None:
        raw = "GITHUB_TOKEN=ghp_" + "Z" * 36 + "\nError: Input required and not supplied: token\n"
        matches = match_patterns.match(raw, self.entries)
        self.assertEqual(matches[0].entry.id, "GEN-01")
        self.assertNotIn("Z" * 36, match_patterns.render_json(matches))

    def test_real_runner_image_failure(self) -> None:
        # Lines from this repo's own first CI run (Python 3.9 on Ubuntu 26.04), which the
        # library initially missed; kept as a regression test for GEN-20.
        log = ("##[error]The version '3.9' with architecture 'x64' was not found for Ubuntu 26.04.\n"
               "Version 3.9 was not found in the local cache\n")
        matches = match_patterns.match(log, self.entries, ["python"])
        self.assertEqual(matches[0].entry.id, "GEN-20")
        self.assertEqual(matches[0].confidence, "high")

    def test_real_missing_asset_failure(self) -> None:
        log = ('Error: unable to locate asset entry in pubspec.yaml: "assets/fonts/GeneralSans-Regular.otf".\n'
               "Error: Failed to build asset bundle\n")
        self.assertEqual(match_patterns.match(log, self.entries, ["flutter"])[0].entry.id, "FLUTTER-18")

    def test_representative_lines(self) -> None:
        for entry_id, (stack, log) in REPRESENTATIVE.items():
            with self.subTest(entry=entry_id):
                md = clean_logs.render_markdown(clean_logs.digest_text(log + "\n##[error]Process completed with exit code 1."))
                matches = match_patterns.match(md, self.entries, [stack])
                self.assertTrue(matches, "no match at all")
                self.assertEqual(matches[0].entry.id, entry_id, [(m.entry.id, m.score) for m in matches])

    def test_specific_beats_generic(self) -> None:
        self.assertGreater(match_patterns.specificity(r"Exceeded timeout of \d+ ?ms for a test"),
                           match_patterns.specificity(r"^\s*●\s+\S.*›"))

    def test_no_match_message(self) -> None:
        self.assertIn("No known pattern matched", match_patterns.render_markdown([]))

    def test_show_and_list(self) -> None:
        out = subprocess.run([sys.executable, str(SCRIPTS / "match_patterns.py"), "--show", "NODE-01"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("lockfile", out.stdout.lower())
        out = subprocess.run([sys.executable, str(SCRIPTS / "match_patterns.py"), "--list"],
                             capture_output=True, text=True)
        self.assertGreaterEqual(len(out.stdout.splitlines()), 100)

    def test_references_exist_for_every_stack(self) -> None:
        import detect_stack

        for ref in detect_stack.REFERENCES.values():
            self.assertTrue((SKILL / "references" / ref).is_file(), ref)


if __name__ == "__main__":
    unittest.main()
