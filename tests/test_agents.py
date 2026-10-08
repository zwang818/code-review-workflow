import json
import tempfile
import unittest
from pathlib import Path

from workflow_agent.agents.review import ReviewTools
from workflow_agent.config import WorkflowSettings
from dataclasses import replace
from workflow_agent.contracts import NodeContext
from workflow_agent.demo import BUGGY_CODE, CLEAN_CODE
from workflow_agent.runner import WorkflowRunner
from workflow_agent.validators import digest
from tests.helpers import ScenarioClient


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = WorkflowSettings(output_dir=self.temp.name)

    def test_invalid_generation_is_corrected_once_and_counted(self):
        class Correctable(ScenarioClient):
            sent_invalid = False
            def complete(self, messages, tools=None, tool_choice="none"):
                if not self.sent_invalid:
                    self.sent_invalid = True
                    return {"role": "assistant", "content": "not json"}
                return super().complete(messages, tools, tool_choice)
        result = WorkflowRunner(Correctable(), self.settings).run("test")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.state.model_calls, 7)
        events = (result.run_dir / "events.jsonl").read_text()
        self.assertIn("contract_correction", events)

    def test_invalid_output_cannot_retry_forever(self):
        class Invalid:
            def complete(self, *args):
                return {"role": "assistant", "content": "not-json"}
        result = WorkflowRunner(Invalid(), self.settings).run("test")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.state.model_calls, 2)
        self.assertEqual(result.state.code_version, 0)

    def test_unknown_repair_id_rejected_before_code_is_saved(self):
        class UnknownID(ScenarioClient):
            def complete(self, messages, tools=None, tool_choice="none"):
                message = super().complete(messages, tools, tool_choice)
                if messages[0]["content"].startswith("ROLE: repair"):
                    data = json.loads(message["content"])
                    data["changes"][0]["issue_id"] = "not-known"
                    message["content"] = json.dumps(data)
                return message
        result = WorkflowRunner(UnknownID(), self.settings).run("test", BUGGY_CODE)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.state.code_version, 1)
        self.assertFalse((result.run_dir / "code/v002.py").exists())

    def test_first_review_must_use_tools(self):
        class NoTools(ScenarioClient):
            def complete(self, *args):
                return {"role": "assistant", "content": "{}"}
        result = WorkflowRunner(NoTools(), self.settings).run("test", CLEAN_CODE)
        self.assertEqual(result.exit_code, 1)

    def test_review_contract_correction_reuses_existing_tool_evidence(self):
        class WrongVersionOnce(ScenarioClient):
            wrong = False
            def complete(self, messages, tools=None, tool_choice="none"):
                message = super().complete(messages, tools, tool_choice)
                if messages[0]["content"].startswith("ROLE: review") and not message.get("tool_calls") and not self.wrong:
                    self.wrong = True
                    data = json.loads(message["content"])
                    data["code_version"] = 999
                    message["content"] = json.dumps(data)
                return message
        result = WorkflowRunner(WrongVersionOnce(), self.settings).run("test", CLEAN_CODE)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((result.state.model_calls, result.state.tool_calls), (3, 2))

    def test_tool_budget_does_not_orphan_unexecuted_calls(self):
        settings = replace(self.settings, model=replace(self.settings.model, max_tool_calls=1))
        client = ScenarioClient()
        result = WorkflowRunner(client, settings).run("test", CLEAN_CODE)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.state.tool_calls, 1)
        final = client.requests[-1][0]
        outputs = [m for m in final if m["role"] == "tool"]
        self.assertEqual(len(outputs), 2)
        self.assertFalse(json.loads(outputs[-1]["content"])["ok"])

    def test_model_cannot_keep_calling_tools_after_final_budget(self):
        class AlwaysCalls(ScenarioClient):
            def complete(self, messages, tools=None, tool_choice="none"):
                from tests.helpers import call
                context = json.loads(messages[1]["content"])
                return call(arguments=json.dumps({"path": context["code_path"]}))
        settings = replace(self.settings, model=replace(self.settings.model, max_rounds=1))
        result = WorkflowRunner(AlwaysCalls(), settings).run("test", CLEAN_CODE)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.state.model_calls, 2)

    def test_roles_have_independent_system_prompts(self):
        client = ScenarioClient()
        WorkflowRunner(client, self.settings).run("test")
        roles = {messages[0]["content"].splitlines()[0] for messages, _, _ in client.requests}
        self.assertEqual(roles, {"ROLE: generate", "ROLE: review", "ROLE: repair"})
        for messages, _, _ in client.requests:
            self.assertEqual(sum(m["role"] == "system" for m in messages), 1)

    def test_tools_restrict_current_version_and_verify_digest(self):
        root = Path(self.temp.name)
        (root / "a.py").write_text("x=1\n")
        (root / "b.py").write_text("private=1\n")
        ctx = NodeContext("test", source="x=1\n", code_path="a.py", code_sha256=digest("x=1\n"))
        tools = ReviewTools(root, ctx)
        self.assertFalse(tools.execute("read_file", '{"path":"b.py"}')["ok"])
        self.assertTrue(tools.execute("read_file", '{"path":"a.py"}')["ok"])
        (root / "a.py").write_text("x=2\n")
        self.assertFalse(tools.execute("read_file", '{"path":"a.py"}')["ok"])


if __name__ == "__main__":
    unittest.main()
