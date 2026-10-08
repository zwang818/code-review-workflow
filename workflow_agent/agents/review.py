"""Tool-using review node with independently verified read coverage."""

import json
from pathlib import Path

from .base import WorkflowNode
from ..contracts import NodeResult, validate_review
from ..errors import AgentError, ModelError, ToolError
from ..tools import TOOL_SCHEMAS, ToolRegistry
from ..validators import digest

REVIEW_SCHEMAS = [s for s in TOOL_SCHEMAS if s["function"]["name"] in {"read_file", "analyze_python"}]


class ReviewTools:
    def __init__(self, root: Path, context):
        self.registry, self.context = ToolRegistry(root), context
        self.covered, self.ast_seen = set(), False

    def execute(self, name, raw):
        try:
            if not isinstance(raw, str) or len(raw) > 20000:
                raise ToolError("工具参数为空或超过20000字符。")
            arguments = json.loads(raw)
            if not isinstance(arguments, dict):
                raise ToolError("参数必须是 JSON 对象。")
            path = arguments.get("path")
            if name not in {"read_file", "analyze_python"}:
                raise ToolError("审查节点只能使用源码读取和 AST 工具。")
            selected = self.registry.resolve(path)
            if selected != self.registry.resolve(self.context.code_path):
                raise ToolError("工具只能访问当前待审查的代码版本。")
            _, source = self.registry._source(path)
            if digest(source) != self.context.code_sha256:
                raise ToolError("当前代码文件与状态摘要不一致，请重新运行。")
        except (ValueError, TypeError, ToolError, OSError, RecursionError) as exc:
            return {"ok": False, "error": str(exc) if isinstance(exc, ToolError) else "工具参数无效。"}
        result = self.registry.execute(name, arguments)
        if result["ok"]:
            data = result["data"]
            if name == "read_file":
                self.covered.update(set(range(data["start_line"], data["end_line"] + 1)) - set(data["clipped_lines"]))
            else:
                self.ast_seen = True
        return result

    def complete_coverage(self) -> bool:
        return self.ast_seen and set(range(1, len(self.context.source.splitlines()) + 1)) <= self.covered


class ReviewAgent(WorkflowNode):
    name = "review"

    def __init__(self, client, settings, store):
        super().__init__(client)
        self.settings, self.store = settings, store

    def run(self, context) -> NodeResult:
        tools = ReviewTools(self.store.root, context)
        messages = self.messages(context.payload(include_source=False))
        count = 0
        for index in range(self.settings.max_rounds + 1):
            final = index == self.settings.max_rounds or count >= self.settings.max_tool_calls
            request = list(messages)
            if final:
                request.append({"role": "system", "content":
                    "工具预算已耗尽，使用现有证据输出 JSON；读取不完整时 coverage 必须为 partial。"})
            choice = "none" if final else ("required" if index == 0 else "auto")
            message = self.client.complete(request, REVIEW_SCHEMAS, choice)
            calls = message.get("tool_calls") or []
            if choice == "required" and not calls:
                raise ModelError("首次审查必须调用真实读取工具，模型未遵守工具协议。")
            if final and calls:
                raise ModelError("模型忽略了禁止继续调用工具的预算约束。")
            if not calls:
                data = self.parse_with_correction(messages, message,
                                                  lambda value: validate_review(value, context), REVIEW_SCHEMAS)
                if not tools.complete_coverage():
                    data["coverage"] = "partial"
                    data["limitations"].append("程序未核实完整源码读取及 AST 调用，不能视为完整审查。")
                data["code_sha256"] = context.code_sha256
                data["tool_calls"] = count
                data["tool_budget_reached"] = final
                return NodeResult(self.name, data)
            messages.append(message)
            for call in calls:
                name = call["function"]["name"]
                if count >= self.settings.max_tool_calls:
                    result = {"ok": False, "error": "节点工具预算耗尽，此调用未执行。"}
                else:
                    result = tools.execute(name, call["function"]["arguments"])
                    count += 1
                    self.client.state.tool_calls += 1
                self.client.logger.emit("tool_result", tool=name[:80], ok=result["ok"])
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
        raise AgentError("审查节点未正常结束。")
