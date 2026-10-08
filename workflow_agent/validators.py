"""Parse source without executing it; keep syntax and runtime results separate."""

import ast
import hashlib


def digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_syntax(source: str, code_version: int) -> dict:
    result = {"code_version": code_version, "code_sha256": digest(source),
              "syntax": "passed", "runtime_tests": "not_run", "diagnostic": None}
    try:
        tree = ast.parse(source)
        compile(tree, "<workflow-source>", "exec")  # Compile for contextual syntax, never exec.
    except (SyntaxError, ValueError, RecursionError) as exc:
        result["syntax"] = "failed"
        result["diagnostic"] = {"line": max(1, min(getattr(exc, "lineno", 1) or 1,
                                                     max(1, len(source.splitlines())))),
                                "message": getattr(exc, "msg", "源码无法解析或嵌套过深。")}
    return result
