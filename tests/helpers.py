"""Scripted protocol participants, deliberately not model-quality evaluators."""

import copy
import json

from workflow_agent.demo import BUGGY_CODE, CLEAN_CODE


def call(name="read_file", arguments='{"path":"a.py"}', identifier="call_1"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": identifier, "type": "function",
                            "function": {"name": name, "arguments": arguments}}]}


def response(data):
    return {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)}


class ScenarioClient:
    def __init__(self, generated=BUGGY_CODE, issues=None, repair=None, only_ast=False):
        self.generated = generated
        self.issue_decider = issues or (lambda ctx, source: "orders=[]" in source)
        self.repair = repair or (lambda ctx: CLEAN_CODE)
        self.only_ast, self.requests = only_ast, []

    def complete(self, messages, tools=None, tool_choice="none"):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools), tool_choice))
        role = messages[0]["content"].splitlines()[0].split(": ")[1]
        context = json.loads(messages[1]["content"])
        if role == "generate":
            return response({"code": self.generated, "summary": "测试脚本源码", "assumptions": []})
        if role == "repair":
            return response({"code": self.repair(context), "changes": [
                {"issue_id": item["id"], "action": "changed", "explanation": "测试脚本修改"}
                for item in context["issues"]], "remaining_notes": []})
        outputs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not outputs and tool_choice != "none":
            names = ("analyze_python",) if self.only_ast else ("read_file", "analyze_python")
            calls = []
            for index, name in enumerate(names):
                calls += call(name, json.dumps({"path": context["code_path"]}), "tool_%d" % index)["tool_calls"]
            return {"role": "assistant", "content": None, "tool_calls": calls}
        read = next((r["data"] for r in outputs if r["ok"] and "content" in r["data"]), None)
        source = "\n".join(line.split(" | ", 1)[1] for line in read["content"].splitlines()) if read else ""
        findings = []
        if read and self.issue_decider(context, source):
            findings = [{"line_start": 1, "line_end": 1, "severity": "medium", "title": "测试问题",
                         "evidence": source.splitlines()[0], "trigger": "测试场景", "impact": "需要修改",
                         "suggestion": "按输入要求修复"}]
        return response({"code_version": context["code_version"], "coverage": "complete",
                         "scope_note": "测试脚本结果", "issues": findings, "limitations": ["未运行代码"]})
