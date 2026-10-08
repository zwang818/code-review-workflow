"""Read-only tools with root confinement, resource limits and AST facts."""

import ast
import io
import json
import os
import tokenize
from pathlib import Path
from typing import Any, Dict

from .errors import ToolError

MAX_FILE_BYTES = 128 * 1024
MAX_READ_LINES = 300
MAX_READ_CHARS = 16000
MAX_FILES = 100
MAX_ENTRIES = 5000
MAX_AST_ITEMS = 100
IGNORED_DIRS = {"venv", "env", "node_modules", "__pycache__", "build", "dist", "reports"}


def schema(name: str, description: str, properties: dict, required: list) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}


PATH_PROPERTY = {"type": "string", "description": "相对审查根目录的路径；不能越界。"}
TOOL_SCHEMAS = [
    schema("list_files", "列出目录中的 Python 文件；隐藏目录和依赖目录被排除。",
           {"path": PATH_PROPERTY}, ["path"]),
    schema("read_file", "读取 Python 文件并附真实行号；截断时可用 next_line 继续。",
           {"path": PATH_PROPERTY,
            "start_line": {"type": "integer", "minimum": 1, "description": "起始行，默认1。"},
            "end_line": {"type": "integer", "minimum": 1, "description": "结束行，最多读取300行。"}},
           ["path"]),
    schema("analyze_python", "静态解析 Python AST，返回语法、结构和有限规则线索，不执行代码。",
           {"path": PATH_PROPERTY}, ["path"]),
]


class ToolRegistry:
    def __init__(self, root: Path):
        try:
            self.root = root.resolve()
        except (OSError, RuntimeError) as exc:
            raise ToolError("无法解析审查根目录，请检查路径及符号链接。") from exc
        if not self.root.is_dir():
            raise ToolError("审查根目录不存在或不是目录。")

    def resolve(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            raise ToolError("path 必须是非空路径字符串。")
        requested = Path(path)
        lexical = requested if requested.is_absolute() else self.root / requested
        try:
            resolved = lexical.resolve()
        except (OSError, RuntimeError) as exc:
            raise ToolError("无法解析路径，请检查路径及符号链接。") from exc
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("路径超出审查根目录（包括指向目录外的符号链接）。") from exc
        # Also check the lexical name, so a hidden alias cannot bypass exclusion.
        parts = relative.parts + requested.parts
        if any((part.startswith(".") and part not in (".", "..")) or part in IGNORED_DIRS
               for part in parts):
            raise ToolError("隐藏文件、依赖目录和输出目录不在审查范围内。")
        if not resolved.exists():
            raise ToolError("路径不存在，请检查文件名和 --root。")
        return resolved

    def _source(self, path: str):
        resolved = self.resolve(path)
        if not resolved.is_file() or resolved.suffix.lower() != ".py":
            raise ToolError("仅支持普通 .py 文件。")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ToolError("文件超过 128 KiB 限制，请拆分后审查。")
        with resolved.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ToolError("文件超过 128 KiB 限制。")
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding)
        except (SyntaxError, UnicodeError, LookupError) as exc:
            raise ToolError("无法按 Python 编码声明解码文件，请检查编码。") from exc
        return resolved.relative_to(self.root).as_posix(), source

    def execute(self, name: str, arguments: Any) -> Dict[str, Any]:
        """Every expected tool failure is returned as data for the next model turn."""
        try:
            if isinstance(arguments, str):
                if len(arguments) > 20000:
                    raise ToolError("工具参数过长。")
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ToolError("工具参数必须是 JSON 对象。")
            spec = next((s["function"] for s in TOOL_SCHEMAS if s["function"]["name"] == name), None)
            if spec is None:
                raise ToolError("未知工具。")
            params = spec["parameters"]
            if set(arguments) - set(params["properties"]):
                raise ToolError("存在未知工具参数。")
            if any(key not in arguments for key in params["required"]):
                raise ToolError("缺少必填参数 path。")
            for key, value in arguments.items():
                kind = params["properties"][key]["type"]
                if kind == "string" and not isinstance(value, str):
                    raise ToolError("%s 必须是字符串。" % key)
                if kind == "integer" and (type(value) is not int or value < 1):
                    raise ToolError("%s 必须是正整数。" % key)
            data = getattr(self, name)(**arguments)
            return {"ok": True, "data": data}
        except (ToolError, OSError, ValueError, RuntimeError, RecursionError) as exc:
            # OS messages can include absolute paths; return a bounded summary instead.
            message = str(exc) if isinstance(exc, ToolError) else "工具执行失败：%s。" % type(exc).__name__
            return {"ok": False, "error": message}

    def list_files(self, path: str) -> dict:
        target = self.resolve(path)
        if target.is_file():
            self._source(path)
            return {"files": [target.relative_to(self.root).as_posix()], "truncated": False}
        files, visited, truncated, walk_errors = [], 0, False, []
        for current, dirs, names in os.walk(target, followlinks=False, onerror=walk_errors.append):
            visited += len(dirs) + len(names)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in IGNORED_DIRS
                             and not (Path(current) / d).is_symlink())
            for name in sorted(names):
                candidate = Path(current) / name
                if name.startswith(".") or candidate.suffix.lower() != ".py" or candidate.is_symlink():
                    continue
                if len(files) == MAX_FILES:
                    truncated = True
                    break
                files.append(candidate.relative_to(self.root).as_posix())
            if truncated or visited >= MAX_ENTRIES:
                truncated = True
                break
        return {"files": files, "truncated": truncated,
                "inaccessible_directories": len(walk_errors),
                "limits": {"files": MAX_FILES, "entries": MAX_ENTRIES}}

    def read_file(self, path: str, start_line: int = 1, end_line: int = None) -> dict:
        relative, source = self._source(path)
        lines = source.splitlines()
        if start_line > len(lines) and lines:
            raise ToolError("start_line 超出文件总行数。")
        end_line = end_line if end_line is not None else start_line + MAX_READ_LINES - 1
        if end_line < start_line:
            raise ToolError("end_line 不能小于 start_line。")
        stop = min(end_line, start_line + MAX_READ_LINES - 1, len(lines))
        rendered, clipped_lines, size = [], [], 0
        for line_no in range(start_line, stop + 1):
            raw = lines[line_no - 1]
            text = "%4d | %s" % (line_no, raw[:2000])
            if size + len(text) + 1 > MAX_READ_CHARS:
                break
            if len(raw) > 2000:
                clipped_lines.append(line_no)
                text += " … [超长行已截断]"
            rendered.append(text)
            size += len(text) + 1
        actual_end = start_line + len(rendered) - 1
        return {"path": relative, "total_lines": len(lines), "start_line": start_line,
                "end_line": actual_end, "content": "\n".join(rendered),
                "truncated": actual_end < len(lines) or bool(clipped_lines),
                "next_line": actual_end + 1 if actual_end < len(lines) else None,
                "clipped_lines": clipped_lines}

    def analyze_python(self, path: str) -> dict:
        relative, source = self._source(path)
        try:
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError) as exc:
            return {"path": relative, "syntax_ok": False,
                    "syntax_error": {"line": getattr(exc, "lineno", 1) or 1,
                                     "column": getattr(exc, "offset", None),
                                     "message": getattr(exc, "msg", "源文件包含无效字符。")},
                    "functions": [], "classes": [], "findings": []}
        functions, classes, findings = [], [], []

        def finding(node, code, severity, title, trigger, impact, suggestion):
            findings.append({"code": code, "line": node.lineno, "severity": severity,
                             "title": title, "trigger": trigger, "impact": impact,
                             "suggestion": suggestion, "source": "AST rule"})

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({"name": node.name, "line": node.lineno,
                                  "end_line": node.end_lineno,
                                  "arguments": [a.arg for a in node.args.posonlyargs + node.args.args
                                                + node.args.kwonlyargs]})
                for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                    mutable = isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                        and default.func.id in {"list", "dict", "set"})
                    if mutable:
                        finding(default, "MUTABLE_DEFAULT", "中", "可变对象作为默认参数",
                                "多次省略该参数调用函数，且函数修改该对象时。",
                                "各次调用可能共享状态，造成结果互相污染。",
                                "默认值改为 None，并在函数体内创建新对象；核实是否有意共享状态。")
            elif isinstance(node, ast.ClassDef):
                classes.append({"name": node.name, "line": node.lineno})
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    finding(node, "BARE_EXCEPT", "中", "裸 except 捕获范围过宽",
                            "try 块抛出 KeyboardInterrupt、SystemExit 等退出信号时。",
                            "退出信号也会被捕获，可能阻止程序正常终止。",
                            "捕获明确的异常类型，通常至少使用 except Exception。")
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    finding(node, "SILENT_EXCEPTION", "中", "异常被静默忽略",
                            "try 块发生异常并命中该处理器时。",
                            "错误信息丢失，调用方可能误以为操作成功。",
                            "记录必要上下文，返回明确失败或重新抛出异常。")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                finding(node, "DYNAMIC_EXECUTION", "高", "动态执行代码，需要核实输入来源",
                        "当传入内容受不可信用户控制，且 eval/exec 未被同名函数覆盖时。",
                        "可能执行非预期代码；风险取决于实际数据流。",
                        "核实输入来源；解析字面量可考虑 ast.literal_eval，其他需求使用明确解析器。")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(node.right, ast.Constant) and type(node.right.value) in (int, float) and node.right.value == 0:
                    finding(node, "ZERO_DIVISION", "高", "除数为字面量零",
                            "执行到该表达式且左操作数使用内置数值运算时。",
                            "触发 ZeroDivisionError。",
                            "核对计算公式，并在除法或取模之前校验除数。")
        priority = {"高": 0, "中": 1, "低": 2}
        findings.sort(key=lambda item: (priority[item["severity"]], item["line"]))
        return {"path": relative, "syntax_ok": True, "total_lines": len(source.splitlines()),
                "functions": functions[:MAX_AST_ITEMS], "classes": classes[:MAX_AST_ITEMS],
                "findings": findings[:MAX_AST_ITEMS],
                "truncated": any(len(items) > MAX_AST_ITEMS for items in (functions, classes, findings)),
                "note": "规则线索需要结合源码核实；未运行代码，未做跨文件数据流分析。"}
