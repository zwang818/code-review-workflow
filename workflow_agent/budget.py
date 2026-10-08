"""One logical-call budget shared by every node and correction request."""

import copy
import json

from .errors import AgentError, BudgetExceeded
from .llm import normalize_message


class MeteredClient:
    def __init__(self, client, settings, state, logger, checkpoint):
        self.client, self.settings, self.state = client, settings, state
        self.logger, self.checkpoint = logger, checkpoint
        if hasattr(client, "on_retry"):
            client.on_retry = self.on_retry

    def on_retry(self, message: str) -> None:
        self.state.http_retries += 1
        self.logger.emit("http_retry", attempt=self.state.http_retries)
        self.checkpoint()

    def complete(self, messages: list, tools=None, tool_choice: str = "none") -> dict:
        if self.state.model_calls >= self.settings.max_model_calls:
            raise BudgetExceeded("全局逻辑模型调用预算已耗尽。")
        if len(json.dumps(messages, ensure_ascii=False)) > self.settings.model.max_context_chars:
            raise AgentError("当前节点超过上下文字符预算，请缩小源码或任务范围。")
        self.state.model_calls += 1
        self.logger.emit("model_request", number=self.state.model_calls, tool_choice=tool_choice)
        self.checkpoint()
        message = self.client.complete(copy.deepcopy(messages), tools or [], tool_choice)
        return normalize_message({"choices": [{"finish_reason": "stop", "message": message}]})
