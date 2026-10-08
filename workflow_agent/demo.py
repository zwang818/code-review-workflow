"""Explicit fixture adapter, not a language model or a general-purpose fixer."""

import json
import uuid

from .errors import AgentError

DEMO_REQUIREMENT = "编写 add_order(item, orders=None)。返回包含新增 item 的新列表，不修改传入列表。连续省略 orders 调用时，各次结果必须独立。仅使用 Python 标准库。"
BUGGY_CODE = '''"""离线教学反例，故意保留共享状态与原地修改问题。"""

def add_order(item, orders=[]):
    orders.append(item)
    return orders
'''
CLEAN_CODE = '''"""订单列表处理：保留输入列表，每次返回独立结果。"""

def add_order(item, orders=None):
    result = [] if orders is None else list(orders)
    result.append(item)
    return result
'''


class DemoClient:
    @staticmethod
    def validate_input(requirement: str, source=None):
        if "".join(requirement.split()) != "".join(DEMO_REQUIREMENT.split()):
            raise AgentError("离线模式仅支持 examples/requirement.txt 中的固定 add_order 任务；其他任务请使用真实模型。")
        if source is not None and source.strip() not in {BUGGY_CODE.strip(), CLEAN_CODE.strip()}:
            raise AgentError("离线审查仅支持项目提供的 buggy_order.py 和 clean_order.py 固定样例。")

    @staticmethod
    def _response(data):
        return {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}

    def complete(self, messages, tools=None, tool_choice="none"):
        role = messages[0]["content"].splitlines()[0].split(": ", 1)[1]
        context = json.loads(messages[1]["content"])
        self.validate_input(context["requirement"])
        if role == "generate":
            return self._response({"code": BUGGY_CODE, "summary": "返回固定教学反例以演示修复流程，没有调用 LLM。",
                                   "assumptions": ["离线模式只处理预设样例。"]})
        if role == "repair":
            self.validate_input(context["requirement"], context["source"])
            return self._response({"code": CLEAN_CODE,
                "changes": [{"issue_id": issue["id"], "action": "changed",
                             "explanation": "固定修正版使用 None 默认值并创建输入列表的副本。"}
                            for issue in context["issues"]], "remaining_notes": ["未运行生成代码。"]})
        observations = []
        for message in messages:
            if message["role"] == "tool":
                observations.append(json.loads(message["content"]))
        if not observations and tool_choice != "none":
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "demo_" + uuid.uuid4().hex, "type": "function",
                 "function": {"name": name, "arguments": json.dumps({"path": context["code_path"]})}}
                for name in ("read_file", "analyze_python")
            ]}
        read = next((r["data"] for r in observations if r["ok"] and "content" in r["data"]), None)
        issues = []
        if read and not read["truncated"]:
            source = "\n".join(line.split(" | ", 1)[1] for line in read["content"].splitlines())
            self.validate_input(context["requirement"], source)
            if source.strip() == BUGGY_CODE.strip():
                for line, title, trigger, impact, suggestion in (
                    (3, "可变默认参数共享状态", "连续省略 orders 调用并修改列表时。", "各次结果互相污染。", "使用 None 默认值。"),
                    (4, "原地修改传入列表", "调用者传入已有列表时。", "违反不修改输入的需求。", "创建列表副本后添加元素。"),
                ):
                    issues.append({"line_start": line, "line_end": line, "severity": "medium",
                                   "title": title, "evidence": source.splitlines()[line - 1],
                                   "trigger": trigger, "impact": impact, "suggestion": suggestion})
        return self._response({"code_version": context["code_version"],
            "coverage": "complete" if read and not read["truncated"] else "partial",
            "scope_note": "固定样例读取后按预设规则检查，没有 LLM 推理。", "issues": issues,
            "limitations": ["离线演示仅覆盖两个固定样例，没有执行 Python 业务代码。"]})
