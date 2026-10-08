import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from workflow_agent.runner import WorkflowRunner
from workflow_agent.config import WorkflowSettings
from workflow_agent.demo import DEMO_REQUIREMENT, DemoClient
from workflow_agent.config import Settings, load_dotenv
from workflow_agent.errors import ConfigError, ModelError
from workflow_agent.llm import ChatClient, normalize_message
from workflow_agent.tools import ToolRegistry
from tests.helpers import call


def envelope(message=None):
    return {"choices": [{"finish_reason": "stop", "message": message or
                         {"role": "assistant", "content": "审查完成"}}]}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.replies, self.requests = [], []
        self.responder = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append((self.path, json.loads(body)))
                status, payload = owner.responder(json.loads(body)) if owner.responder else owner.replies.pop(0)
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/redirected")
                self.end_headers()
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.settings = Settings(api_key="test-key-not-real", model="test-model",
                                 base_url="http://127.0.0.1:%d/v1" % self.server.server_port)

    def test_real_http_workflow_loop_and_tool_pairing(self):
        fixture = DemoClient()
        self.responder = lambda data: (200, envelope(fixture.complete(data["messages"], data.get("tools", []), data.get("tool_choice", "none"))))
        with tempfile.TemporaryDirectory() as directory:
            settings = WorkflowSettings(model=self.settings, output_dir=directory)
            result = WorkflowRunner(ChatClient(self.settings), settings).run(DEMO_REQUIREMENT)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.state.model_calls, 6)
        self.assertEqual(self.requests[0][0], "/v1/chat/completions")
        self.assertNotIn("tools", self.requests[0][1])
        self.assertNotIn("tool_choice", self.requests[0][1])
        final = self.requests[-1][1]
        outputs = [m for m in final["messages"] if m["role"] == "tool"]
        calls = [c for m in final["messages"] for c in m.get("tool_calls", [])]
        self.assertEqual({m["tool_call_id"] for m in outputs}, {c["id"] for c in calls})
        self.assertIn("code/v002.py", outputs[0]["content"])

    def test_transient_http_errors_retry(self):
        self.replies.extend([(429, b"rate-limited"), (503, b"unavailable"), (200, envelope())])
        events = []
        with patch("workflow_agent.llm.time.sleep") as sleep:
            message = ChatClient(self.settings, events.append).complete([], [])
        self.assertEqual(message["content"], "审查完成")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(events), 2)

    def test_http_retry_is_recorded_in_workflow_state(self):
        fixture = DemoClient()
        def responder(data):
            if len(self.requests) == 1:
                return 503, b"temporary"
            return 200, envelope(fixture.complete(data["messages"], data.get("tools", []), data.get("tool_choice", "none")))
        self.responder = responder
        with tempfile.TemporaryDirectory() as directory, patch("workflow_agent.llm.time.sleep"):
            settings = WorkflowSettings(model=self.settings, output_dir=directory)
            result = WorkflowRunner(ChatClient(self.settings), settings).run(DEMO_REQUIREMENT)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((result.state.model_calls, result.state.http_retries), (6, 1))
        self.assertEqual(len(self.requests), 7)

    def test_auth_failure_does_not_retry_or_echo_body(self):
        self.replies.append((401, b"private-upstream-message"))
        with self.assertRaises(ModelError) as raised:
            ChatClient(self.settings).complete([], [])
        self.assertIn("401", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(len(self.requests), 1)

    def test_retry_budget_is_finite(self):
        self.replies.extend([(500, b"failure")] * 3)
        with patch("workflow_agent.llm.time.sleep"), self.assertRaises(ModelError):
            ChatClient(self.settings).complete([], [])
        self.assertEqual(len(self.requests), 3)

    def test_redirect_is_not_followed(self):
        self.replies.append((302, b"redirect"))
        with self.assertRaises(ModelError):
            ChatClient(self.settings).complete([], [])
        self.assertEqual(len(self.requests), 1)

    def test_invalid_json_and_oversized_response(self):
        self.replies.extend([(200, b"<html>not api</html>"), (200, b"x" * 100)])
        with self.assertRaises(ModelError):
            ChatClient(self.settings).complete([], [])
        with patch("workflow_agent.llm.MAX_RESPONSE_BYTES", 64), self.assertRaises(ModelError):
            ChatClient(self.settings).complete([], [])

    def test_timeout_retries_are_bounded(self):
        client = ChatClient(self.settings)
        with patch.object(client.opener, "open", side_effect=TimeoutError) as opened, \
                patch("workflow_agent.llm.time.sleep"), self.assertRaises(ModelError):
            client.complete([], [])
        self.assertEqual(opened.call_count, 3)


class ProtocolAndConfigTests(unittest.TestCase):
    def test_invalid_envelopes_fail_cleanly(self):
        invalid = [{}, {"choices": []}, {"choices": [None]}, {"choices": "bad"},
                   envelope({"role": "user", "content": "bad"}),
                   envelope({"role": "assistant", "content": None}),
                   envelope({"role": "assistant", "content": []})]
        duplicate = call()
        duplicate["tool_calls"] *= 2
        invalid.append(envelope(duplicate))
        truncated = envelope()
        truncated["choices"][0]["finish_reason"] = "length"
        invalid.append(truncated)
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ModelError):
                normalize_message(response)

    def test_invalid_function_arguments_remain_recoverable(self):
        message = normalize_message(envelope(call(arguments="{not-json")))
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], "{not-json")

    def test_missing_credentials_and_invalid_numbers(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                Settings.from_env()
            Settings.from_env(require_api=False)
        for value in ("oops", "nan", "0", "301"):
            with patch.dict(os.environ, {"AGENT_TIMEOUT": value}, clear=True), self.assertRaises(ConfigError):
                Settings.from_env(require_api=False)

    def test_no_plain_http_credentials_to_remote(self):
        settings = Settings(api_key="secret", model="test")
        for url in ("http://example.com/v1", "https://user:pass@example.com/v1",
                    "https://example.com/v1?secret=1", "not-a-url"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                replace(settings, base_url=url).validate()

    def test_dotenv_does_not_execute_or_override_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EXISTING": "old"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text("# config\nEXISTING=new\nQUOTED='hello world'\nLITERAL=$(whoami)\n")
            load_dotenv(path)
            self.assertEqual(os.environ["EXISTING"], "old")
            self.assertEqual(os.environ["QUOTED"], "hello world")
            self.assertEqual(os.environ["LITERAL"], "$(whoami)")
            path.write_text("BROKEN\n")
            with self.assertRaises(ConfigError):
                load_dotenv(path)


if __name__ == "__main__":
    unittest.main()
