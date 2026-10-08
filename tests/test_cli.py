import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow_agent.cli import main
from workflow_agent.demo import BUGGY_CODE, CLEAN_CODE, DEMO_REQUIREMENT


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "buggy file.py").write_text(BUGGY_CODE)
        (self.root / "clean.py").write_text(CLEAN_CODE)
        (self.root / "requirement.txt").write_text(DEMO_REQUIREMENT)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        common = ["--env-file", str(self.root / ".missing"), "--output-dir", str(self.root / "runs")]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args + common)
        return code, out.getvalue(), err.getvalue()

    def test_generate_demo_and_artifacts(self):
        code, out, err = self.invoke(["run", "--demo", "--task-file", str(self.root / "requirement.txt")])
        self.assertEqual(code, 0)
        self.assertIn("没有调用外部 LLM", out)
        self.assertIn("[产物]", err)
        saved = next((self.root / "runs").glob("*/state.json"))
        self.assertEqual(json.loads(saved.read_text())["code_version"], 2)

    def test_review_preserves_original_and_handles_spaces(self):
        original = (self.root / "buggy file.py").read_bytes()
        code, _, _ = self.invoke(["review", "--demo", "--root", str(self.root),
                                 "--file", "buggy file.py", "--task", DEMO_REQUIREMENT])
        self.assertEqual(code, 0)
        self.assertEqual((self.root / "buggy file.py").read_bytes(), original)

    def test_attention_exit_code(self):
        code, out, _ = self.invoke(["review", "--demo", "--root", str(self.root),
                                   "--file", "buggy file.py", "--task", DEMO_REQUIREMENT, "--max-fixes", "0"])
        self.assertEqual(code, 3)
        self.assertIn("仍需人工处理", out)

    def test_real_mode_missing_key_is_explicit(self):
        code, _, err = self.invoke(["run", "--task", DEMO_REQUIREMENT])
        self.assertEqual(code, 1)
        self.assertIn("OPENAI_API_KEY", err)
        self.assertFalse((self.root / "runs").exists())

    def test_demo_rejects_unrelated_task(self):
        code, _, err = self.invoke(["run", "--demo", "--task", "实现贪吃蛇"])
        self.assertEqual(code, 1)
        self.assertIn("固定", err)
        self.assertFalse((self.root / "runs").exists())

    def test_bad_path_and_bad_budget_fail_cleanly(self):
        for args in (["review", "--demo", "--root", str(self.root), "--file", "../outside.py", "--task", DEMO_REQUIREMENT],
                     ["run", "--demo", "--task", DEMO_REQUIREMENT, "--max-fixes", "4"]):
            with self.subTest(args=args):
                code, _, err = self.invoke(args)
                self.assertEqual(code, 1)
                self.assertNotIn("Traceback", err)

    def test_quiet_has_report_only(self):
        code, out, err = self.invoke(["run", "--demo", "--task", DEMO_REQUIREMENT, "--quiet"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Workflow"))
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
