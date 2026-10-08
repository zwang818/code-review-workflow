import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from workflow_agent.artifacts import ArtifactStore
from workflow_agent.config import Settings, WorkflowSettings
from workflow_agent.demo import BUGGY_CODE, CLEAN_CODE, DEMO_REQUIREMENT, DemoClient
from workflow_agent.errors import AgentError, ModelError
from workflow_agent.runner import WorkflowRunner
from workflow_agent.validators import digest
from tests.helpers import ScenarioClient


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = WorkflowSettings(output_dir=self.temp.name)

    def run_case(self, client=None, source=None, **config):
        return WorkflowRunner(client or ScenarioClient(), replace(self.settings, **config)).run("测试需求", source)

    def test_three_roles_transfer_issues_and_recheck_new_code(self):
        client = ScenarioClient()
        result = self.run_case(client)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((result.state.code_version, result.state.fix_attempt, result.state.review_round), (2, 1, 2))
        repair_context = next(json.loads(m[1]["content"]) for m, _, _ in client.requests
                              if m[0]["content"].startswith("ROLE: repair"))
        self.assertEqual(repair_context["source"], BUGGY_CODE)
        self.assertEqual(repair_context["issues"][0]["id"], "R001-I001")
        second = json.loads((result.run_dir / "reviews/r002.json").read_text())
        self.assertEqual(second["code_sha256"], digest(CLEAN_CODE))
        self.assertEqual((result.run_dir / "final/solution.py").read_text(), CLEAN_CODE)
        self.assertEqual(result.state.validation["runtime_tests"], "not_run")

    def test_clean_input_skips_generate_and_repair(self):
        result = self.run_case(source=CLEAN_CODE)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.state.fix_attempt, 0)
        self.assertEqual({s["name"] for s in result.state.stages if s["status"] == "skipped"}, {"generate", "repair"})

    def test_zero_fix_budget_returns_attention(self):
        result = self.run_case(source=BUGGY_CODE, max_fixes=0)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.state.fix_attempt, 0)
        self.assertTrue(result.state.issues)

    def test_repeated_issues_stop_exactly_at_fix_limit(self):
        client = ScenarioClient(issues=lambda ctx, src: True, repair=lambda ctx: ctx["source"] + "\n# changed\n")
        result = self.run_case(client, BUGGY_CODE, max_fixes=2)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual((result.state.fix_attempt, result.state.review_round, result.state.code_version), (2, 3, 3))
        self.assertEqual(len(list((result.run_dir / "repairs").glob("*.json"))), 2)

    def test_unchanged_repair_stops_without_new_version(self):
        result = self.run_case(ScenarioClient(repair=lambda ctx: ctx["source"]), BUGGY_CODE)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual((result.state.fix_attempt, result.state.code_version), (1, 1))
        self.assertIn("未发生变化", result.state.reason)

    def test_global_budget_does_not_create_false_pass(self):
        client = ScenarioClient()
        result = self.run_case(client, BUGGY_CODE, max_model_calls=1)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(len(client.requests), 1)
        self.assertIsNone(result.state.latest_review_path)
        self.assertIn("尚无完成的审查结果", result.report)

    def test_syntax_error_cannot_be_hidden_by_empty_model_issues(self):
        result = self.run_case(ScenarioClient(issues=lambda ctx, src: False), "return 1\n", max_fixes=0)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.state.validation["syntax"], "failed")
        self.assertEqual(result.state.issues[0]["source"], "syntax_validator")

    def test_syntax_error_can_be_repaired_then_rechecked(self):
        result = self.run_case(ScenarioClient(issues=lambda ctx, src: False), "def broken(:\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.state.validation["syntax"], "passed")
        self.assertEqual(result.state.code_version, 2)

    def test_claimed_complete_without_read_is_downgraded(self):
        result = self.run_case(ScenarioClient(only_ast=True), CLEAN_CODE)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.state.fix_attempt, 0)
        review = json.loads((result.run_dir / "reviews/r001.json").read_text())
        self.assertEqual(review["coverage"], "partial")

    def test_long_line_cannot_be_claimed_fully_read(self):
        result = self.run_case(ScenarioClient(issues=lambda ctx, src: False), "#" + "x" * 4000 + "\n")
        self.assertEqual(result.exit_code, 3)

    def test_model_failure_preserves_stage_and_source(self):
        class FailedClient:
            def complete(self, *args):
                raise ModelError("模拟请求失败")
        result = self.run_case(FailedClient(), BUGGY_CODE)
        saved = json.loads((result.run_dir / "state.json").read_text())
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(saved["error"]["stage"], "review")
        self.assertEqual((result.run_dir / "code/v001.py").read_text(), BUGGY_CODE)

    def test_interrupt_preserves_cancelled_state(self):
        class CancelledClient:
            def complete(self, *args):
                raise KeyboardInterrupt
        result = self.run_case(CancelledClient(), BUGGY_CODE)
        self.assertEqual(result.exit_code, 130)
        self.assertEqual(json.loads((result.run_dir / "state.json").read_text())["status"], "cancelled")

    def test_failed_repair_write_does_not_advance_version(self):
        original = ArtifactStore.write_json
        def fail(store, relative, value, replace=False):
            if relative.startswith("repairs/"):
                raise OSError("disk failure")
            return original(store, relative, value, replace)
        with patch.object(ArtifactStore, "write_json", fail):
            result = self.run_case(source=BUGGY_CODE)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.state.code_version, 1)
        self.assertEqual(result.state.error["stage"], "repair")

    def test_unique_run_directories_and_jsonl_events(self):
        first, second = self.run_case(source=CLEAN_CODE), self.run_case(source=CLEAN_CODE)
        self.assertNotEqual(first.run_dir, second.run_dir)
        events = [json.loads(line) for line in (first.run_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["event"], "workflow_finished")
        self.assertTrue(all(e["time"].endswith("+00:00") for e in events))

    def test_workflow_does_not_execute_top_level_source(self):
        marker = Path(self.temp.name) / "must-not-exist"
        source = "from pathlib import Path\nPath(%r).touch()\n" % str(marker)
        result = self.run_case(ScenarioClient(issues=lambda ctx, src: False), source)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(marker.exists())

    def test_utf8_snapshot_coding_cookie_matches_digest(self):
        source = "# coding: latin-1\nvalue = 'café'\n"
        result = self.run_case(ScenarioClient(issues=lambda ctx, src: False), source)
        self.assertEqual(result.exit_code, 0)
        saved = (result.run_dir / "code/v001.py").read_text(encoding="utf-8")
        self.assertIn("coding: utf-8", saved)
        self.assertEqual(digest(saved), result.state.code_sha256)

    def test_finalization_failure_overrides_success(self):
        original = ArtifactStore.write_text
        def fail(store, relative, content, replace=False):
            if relative == "final/solution.py":
                raise OSError("disk failure")
            return original(store, relative, content, replace)
        with patch.object(ArtifactStore, "write_text", fail):
            result = self.run_case(source=CLEAN_CODE)
        self.assertEqual(result.exit_code, 1)
        self.assertIsNone(result.state.outcome)
        saved = json.loads((result.run_dir / "state.json").read_text())
        self.assertEqual(saved["error"]["stage"], "finalize")

    def test_demo_runs_all_roles_and_is_explicit(self):
        result = WorkflowRunner(DemoClient(), self.settings, demo=True).run(DEMO_REQUIREMENT)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.state.model_calls, 6)
        self.assertEqual(result.state.tool_calls, 4)
        self.assertIn("没有调用外部 LLM", result.report)

    def test_runner_cannot_be_reused(self):
        runner = WorkflowRunner(ScenarioClient(), self.settings)
        runner.run("test", CLEAN_CODE)
        with self.assertRaises(AgentError):
            runner.run("test", CLEAN_CODE)


if __name__ == "__main__":
    unittest.main()
