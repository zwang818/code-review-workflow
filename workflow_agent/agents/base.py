"""Shared JSON correction mechanics; no shared conversation between roles."""

import json
from pathlib import Path

from ..contracts import NodeResult, parse_json
from ..errors import ContractError, ModelError


class WorkflowNode:
    name = "base"

    def __init__(self, client):
        self.client = client

    def messages(self, payload: dict) -> list:
        prompt = (Path(__file__).resolve().parents[1] / "prompts" / (self.name + ".txt")).read_text(encoding="utf-8")
        return [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    def parse_with_correction(self, messages, message, validator, tools=None) -> dict:
        for correction in range(2):
            if message.get("tool_calls"):
                raise ModelError("文本节点或最终结构化响应返回了意外的工具调用。")
            try:
                return validator(parse_json(message.get("content")))
            except ContractError as exc:
                if correction:
                    raise
                messages.append(message)
                messages.append({"role": "user", "content":
                    "输出契约校验失败，请仅修正 JSON 结构或证据，不调用工具：" + str(exc)})
                self.client.logger.emit("contract_correction", node=self.name)
                message = self.client.complete(messages, tools, "none")
        raise ContractError("节点输出无效。")

    def run(self, context) -> NodeResult:
        raise NotImplementedError
