"""Tests for workflow_steps.py: YAML-subset parser and failing-step mapping."""

from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from _helpers import SCRIPTS  # noqa: F401  (adds scripts to sys.path)
import workflow_steps

WORKFLOW = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [main]
      pull_request:
    env:
      NODE_ENV: test   # trailing comment
    jobs:
      test:
        name: Test (${{ matrix.node }})
        runs-on: ubuntu-latest
        strategy:
          fail-fast: false
          matrix:
            node: [18, 20]
        steps:
          - uses: actions/checkout@v4
          - name: Setup node
            uses: actions/setup-node@v4
            with:
              node-version: ${{ matrix.node }}
              cache: npm
          - run: npm ci
          - name: Build
            working-directory: web
            env:
              API_KEY: ${{ secrets.API_KEY }}
            run: |
              npm run build
              echo "done: yes"   # kept: inside a block scalar

              echo after-blank
      deploy:
        runs-on: ubuntu-latest
        steps:
          - name: Folded
            run: >
              echo one
              two
          - name: "Quoted: name"
            run: 'echo hi'
""")


class TestParser(unittest.TestCase):
    def setUp(self) -> None:
        self.data = workflow_steps.parse_yaml(WORKFLOW)

    def test_structure(self) -> None:
        self.assertEqual(self.data["name"], "CI")
        self.assertEqual(list(self.data["on"]), ["push", "pull_request"])
        self.assertEqual(self.data["on"]["push"]["branches"], ["main"])
        self.assertEqual(self.data["env"]["NODE_ENV"], "test")
        self.assertEqual(self.data["jobs"]["test"]["strategy"]["matrix"]["node"], ["18", "20"])

    def test_steps_and_block_scalars(self) -> None:
        steps = self.data["jobs"]["test"]["steps"]
        self.assertEqual(steps[0], {"uses": "actions/checkout@v4"})
        self.assertEqual(steps[1]["with"]["cache"], "npm")
        self.assertEqual(steps[2]["run"], "npm ci")
        self.assertEqual(steps[3]["run"], 'npm run build\necho "done: yes"   # kept: inside a block scalar\n\necho after-blank')
        deploy = self.data["jobs"]["deploy"]["steps"]
        self.assertEqual(deploy[0]["run"], "echo one two")
        self.assertEqual(deploy[1]["name"], "Quoted: name")
        self.assertEqual(deploy[1]["run"], "echo hi")

    def test_display_names(self) -> None:
        steps = self.data["jobs"]["test"]["steps"]
        self.assertEqual(workflow_steps.step_display_name(steps[0]), "Run actions/checkout@v4")
        self.assertEqual(workflow_steps.step_display_name(steps[2]), "Run npm ci")
        self.assertEqual(workflow_steps.step_display_name(steps[3]), "Build")


class TestFindStep(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        wf = Path(self.tmp.name) / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(WORKFLOW, encoding="utf-8")
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_matrix_job_name_and_step(self) -> None:
        info = workflow_steps.find_step(self.repo, "CI", "Test (18)", "Build")
        self.assertEqual(info["job_id"], "test")
        self.assertEqual(info["step_name"], "Build")
        self.assertEqual(info["working_directory"], "web")
        self.assertEqual(info["secrets_referenced"], ["API_KEY"])
        self.assertIn("NODE_ENV", info["env_names"])
        self.assertEqual(info["toolchain"][0]["tool"], "node")
        self.assertIn("cd web", info["repro_script"])
        self.assertGreater(info["match_confidence"], 0.8)

    def test_unnamed_step(self) -> None:
        info = workflow_steps.find_step(self.repo, None, "Test (20)", "Run npm ci")
        self.assertEqual(info["step_index"], 3)
        self.assertEqual(info["run"], "npm ci")

    def test_no_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("error", workflow_steps.find_step(Path(d), None, None, "x"))


if __name__ == "__main__":
    unittest.main()
