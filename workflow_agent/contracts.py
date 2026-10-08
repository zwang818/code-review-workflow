"""Validated node contracts. Model data never selects a file path or function."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ContractError

MAX_CODE_CHARS = 20000
MAX_CODE_BYTES = 128 * 1024


def text(value: Any, label: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError("%s 必须是非空字符串，最多%d字符。" % (label, limit))
    return value


def strings(value: Any, label: str) -> list:
    if not isinstance(value, list) or len(value) > 100:
        raise ContractError("%s 必须是最多100项的字符串列表。" % label)
    return [text(item, label) for item in value]


def fields(data: Any, required: set, label: str) -> None:
    if not isinstance(data, dict) or set(data) != required:
        raise ContractError("%s 字段必须恰好为：%s。" % (label, ", ".join(sorted(required))))


def parse_json(content: str) -> dict:
    if not isinstance(content, str) or len(content) > 150000:
        raise ContractError("结构化响应为空或过长。")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        result = json.loads(content, object_pairs_hook=unique,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, RecursionError) as exc:
        raise ContractError("节点必须返回完整 JSON 对象，不要 Markdown 围栏、重复键或 NaN。") from exc
    if not isinstance(result, dict):
        raise ContractError("节点输出必须是 JSON 对象。")
    return result


def code_text(value: Any, generated: bool = True) -> str:
    limit = MAX_CODE_CHARS if generated else MAX_CODE_BYTES
    value = text(value, "code", limit).replace("\r\n", "\n").replace("\r", "\n")
    # Artifacts are UTF-8. Keep Python coding cookies consistent after decoding input.
    lines = value.splitlines(keepends=True)
    for index in range(min(2, len(lines))):
        if lines[index].lstrip().startswith("#"):
            lines[index] = re.sub(r"coding[:=]\s*[-\w.]+", "coding: utf-8", lines[index])
    value = "".join(lines)
    if len(value.encode("utf-8")) > MAX_CODE_BYTES:
        raise ContractError("源码超过128 KiB限制。")
    return value


@dataclass(frozen=True)
class NodeContext:
    requirement: str
    source: str = ""
    code_version: int = 0
    code_path: str = ""
    code_sha256: str = ""
    syntax: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    previous_review: dict = field(default_factory=dict)
    last_repair: dict = field(default_factory=dict)
    review_round: int = 0

    def payload(self, include_source: bool = True) -> dict:
        from dataclasses import asdict
        result = asdict(self)
        if not include_source:
            result.pop("source")
        return result


@dataclass(frozen=True)
class NodeResult:
    node: str
    data: dict


def validate_generation(data: dict) -> dict:
    fields(data, {"code", "summary", "assumptions"}, "编码结果")
    return {"code": code_text(data["code"]), "summary": text(data["summary"], "summary"),
            "assumptions": strings(data["assumptions"], "assumptions")}


def validate_review(data: dict, context: NodeContext) -> dict:
    fields(data, {"code_version", "coverage", "scope_note", "issues", "limitations"}, "审查结果")
    if type(data["code_version"]) is not int or data["code_version"] != context.code_version:
        raise ContractError("审查结果引用了错误代码版本。")
    if data["coverage"] not in ("complete", "partial"):
        raise ContractError("coverage 必须为 complete 或 partial。")
    text(data["scope_note"], "scope_note")
    strings(data["limitations"], "limitations")
    if not isinstance(data["issues"], list) or len(data["issues"]) > 100:
        raise ContractError("issues 必须是最多100项的列表。")
    lines = context.source.splitlines()
    required = {"line_start", "line_end", "severity", "title", "evidence", "trigger", "impact", "suggestion"}
    for issue in data["issues"]:
        fields(issue, required, "问题")
        start, end = issue["line_start"], issue["line_end"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ContractError("问题行号超出当前源码范围。")
        if issue["severity"] not in ("high", "medium", "low"):
            raise ContractError("severity 必须为 high、medium 或 low。")
        for key in required - {"line_start", "line_end", "severity"}:
            text(issue[key], key)
        if issue["evidence"].strip() not in "\n".join(lines[start - 1:end]):
            raise ContractError("问题证据与声明行范围内的源码不匹配。")
    return data


def validate_repair(data: dict, context: NodeContext) -> dict:
    fields(data, {"code", "changes", "remaining_notes"}, "修复结果")
    source = code_text(data["code"])
    strings(data["remaining_notes"], "remaining_notes")
    if not isinstance(data["changes"], list) or len(data["changes"]) > 101:
        raise ContractError("changes 必须是有限列表。")
    known = {issue["id"] for issue in context.issues}
    seen = set()
    for change in data["changes"]:
        fields(change, {"issue_id", "action", "explanation"}, "修改说明")
        identifier = text(change["issue_id"], "issue_id", 100)
        if identifier not in known or identifier in seen:
            raise ContractError("修改说明包含未知或重复的问题 ID。")
        if change["action"] not in ("changed", "not_changed"):
            raise ContractError("action 必须为 changed 或 not_changed。")
        text(change["explanation"], "explanation")
        seen.add(identifier)
    if seen != known:
        raise ContractError("修复说明必须逐项覆盖输入问题 ID。")
    return dict(data, code=source)
