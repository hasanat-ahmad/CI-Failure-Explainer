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
               "flaky-tests.md": 5}


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
