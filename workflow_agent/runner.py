"""Central orchestration: explicit state, sequential nodes, bounded repair loop."""

import copy
import time
from dataclasses import dataclass
from pathlib import Path

from .agents import GenerateAgent, RepairAgent, ReviewAgent
from .artifacts import ArtifactStore
from .budget import MeteredClient
from .contracts import NodeContext, code_text, text
from .errors import AgentError, BudgetExceeded
from .events import EventLogger
from .report import render_report
from .state import WorkflowState
from .validators import digest, validate_syntax


@dataclass
class RunResult:
    state: WorkflowState
    run_dir: Path
    report: str

    @property
    def exit_code(self) -> int:
        if self.state.status == "cancelled":
            return 130
        if self.state.status != "completed":
            return 1
        return 0 if self.state.outcome == "no_issues_found" else 3


class WorkflowRunner:
    def __init__(self, client, settings, demo=False, on_event=None):
        settings.validate()
        self.raw_client, self.settings, self.demo = client, settings, demo
        self.on_event = on_event or (lambda message: None)
        self._used = False

    def checkpoint(self):
        self.store.write_json("state.json", self.state.to_dict(), replace=True)

    def _stage(self, name, function):
        self.state.current_stage = name
        entry = {"name": name, "status": "running", "code_version": self.state.code_version}
        self.state.stages.append(entry)
        started = time.monotonic()
        self.checkpoint()
        self.logger.emit("stage_started")
        try:
            result = function()
            entry["status"] = "completed"
            return result
        except (Exception, KeyboardInterrupt) as exc:
            entry["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            entry["error_type"] = type(exc).__name__
            raise
        finally:
            entry["duration_seconds"] = round(time.monotonic() - started, 6)
            self.checkpoint()
            self.logger.emit("stage_finished", status=entry["status"], duration_seconds=entry["duration_seconds"])

    def _source(self):
        with self.store.path(self.state.code_path).open(encoding="utf-8", newline="") as stream:
            source = stream.read()
        if digest(source) != self.state.code_sha256:
            raise AgentError("代码文件与状态摘要不一致，停止处理。")
        return source

    def _commit_code(self, source):
        version = self.state.code_version + 1
        path = "code/v%03d.py" % version
        self.store.write_text(path, source)
        self.state.code_version, self.state.code_path = version, path
        self.state.code_sha256 = digest(source)
        self.state.validation, self.state.issues = {}, []
        self.state.latest_review_path = None
        self.checkpoint()

    def _context(self):
        return NodeContext(requirement=self.state.requirement,
                           source=self._source() if self.state.code_path else "",
                           code_version=self.state.code_version, code_path=self.state.code_path or "",
                           code_sha256=self.state.code_sha256 or "", syntax=copy.deepcopy(self.state.validation),
                           issues=copy.deepcopy(self.state.issues), review_round=self.state.review_round,
                           previous_review=copy.deepcopy(self.reviews[-1]) if self.reviews else {},
                           last_repair=copy.deepcopy(self.repairs[-1]) if self.repairs else {})

    def _validate(self):
        validation = validate_syntax(self._source(), self.state.code_version)
        self.store.write_json("validations/v%03d.json" % self.state.code_version, validation)
        self.state.validation = validation

    def _review(self):
        self.state.review_round += 1
        context = self._context()
        result = self.nodes["review"].run(context).data
        self._source()  # Detect file changes while the model was working.
        diagnostic = self.state.validation.get("diagnostic")
        if diagnostic:
            line = diagnostic["line"]
            source_lines = context.source.splitlines()
            result["issues"].insert(0, {
                "line_start": line, "line_end": line, "severity": "high", "title": "Python 语法解析失败",
                "evidence": source_lines[line - 1] if source_lines else "",
                "trigger": "Python 解析该文件时。", "impact": diagnostic["message"],
                "suggestion": "修正语法后重新检查。", "source": "syntax_validator"})
        result["issues"].sort(key=lambda issue: ({"high": 0, "medium": 1, "low": 2}[issue["severity"]], issue["line_start"]))
        for index, issue in enumerate(result["issues"], 1):
            issue["id"] = "R%03d-I%03d" % (self.state.review_round, index)
        result["round"] = self.state.review_round
        path = "reviews/r%03d.json" % self.state.review_round
        self.store.write_json(path, result)
        self.state.latest_review_path, self.state.issues = path, copy.deepcopy(result["issues"])
        self.reviews.append(result)

    def _repair(self):
        context = self._context()
        result = self.nodes["repair"].run(context).data
        self._source()
        attempt = self.state.fix_attempt + 1
        result.update(attempt=attempt, input_code_version=context.code_version,
                      input_code_sha256=context.code_sha256)
        self.store.write_json("repairs/f%03d.json" % attempt, result)
        self.state.fix_attempt = attempt
        self.repairs.append(result)
        if digest(result["code"]) == self.state.code_sha256:
            return False
        self._commit_code(result["code"])
        return True

    def _finish(self, outcome, reason):
        self.state.status, self.state.outcome, self.state.reason = "completed", outcome, reason

    def run(self, requirement: str, source=None) -> RunResult:
        if self._used:
            raise AgentError("每个 WorkflowRunner 实例只能运行一次，请创建新实例。")
        requirement = text(requirement, "需求", 12000)
        if source is not None:
            source = code_text(source, generated=False)
        self._used = True
        self.store = ArtifactStore(Path(self.settings.output_dir))
        self.state = WorkflowState(self.store.run_id, "generate" if source is None else "review",
                                   requirement, self.settings.max_fixes, demo=self.demo)
        self.logger = EventLogger(self.store, self.state, self.on_event)
        client = MeteredClient(self.raw_client, self.settings, self.state, self.logger, self.checkpoint)
        self.nodes = {"generate": GenerateAgent(client), "review": ReviewAgent(client, self.settings.model, self.store),
                      "repair": RepairAgent(client)}
        self.reviews, self.repairs = [], []
        try:
            self.store.write_json("input.json", {"mode": self.state.mode, "requirement": requirement,
                "demo": self.demo, "max_fix_attempts": self.settings.max_fixes,
                "max_model_calls": self.settings.max_model_calls})
            self.state.status = "running"
            if source is None:
                def generate():
                    result = self.nodes["generate"].run(self._context()).data
                    self.store.write_json("generation.json", result)
                    self._commit_code(result["code"])
                self._stage("generate", generate)
            else:
                self._stage("snapshot", lambda: self._commit_code(source))
                self.state.stages.append({"name": "generate", "status": "skipped", "code_version": 1,
                                          "duration_seconds": 0})
            while True:
                self._stage("validate", self._validate)
                self._stage("review", self._review)
                if self.reviews[-1]["coverage"] != "complete":
                    self._finish("needs_attention", "当前版本源码获取或审查不完整。")
                    break
                if not self.state.issues and self.state.validation["syntax"] == "passed":
                    self.state.stages.append({"name": "repair", "status": "skipped",
                        "code_version": self.state.code_version, "duration_seconds": 0})
                    self._finish("no_issues_found", "当前版本语法通过且本轮静态审查未发现问题。")
                    break
                if self.state.fix_attempt >= self.settings.max_fixes:
                    self._finish("needs_attention", "修复次数上限已达到，仍有未解决问题。")
                    break
                if not self._stage("repair", self._repair):
                    self._finish("needs_attention", "修复源码未发生变化，停止重复循环。")
                    break
        except BudgetExceeded as exc:
            self._finish("needs_attention", str(exc))
        except (Exception, KeyboardInterrupt) as exc:
            self.state.status = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            self.state.reason = "用户取消。" if isinstance(exc, KeyboardInterrupt) else "流程技术失败。"
            self.state.error = {"stage": self.state.current_stage, "type": type(exc).__name__,
                                "message": str(exc) if isinstance(exc, AgentError) else "执行失败，请检查运行环境与产物。"}
        # Finalization is deterministic and spends no additional model calls.
        report = render_report(self.state, self.reviews, self.repairs)
        try:
            if self.state.code_path:
                self.store.write_text("final/solution.py", self._source())
            self.store.write_text("final/report.md", report)
            self.checkpoint()
            self.logger.emit("workflow_finished", status=self.state.status, outcome=self.state.outcome)
        except (OSError, AgentError) as exc:
            self.state.status, self.state.outcome = "failed", None
            self.state.reason = "最终产物保存失败。"
            self.state.error = {"stage": "finalize", "type": type(exc).__name__, "message": "无法保存最终产物。"}
            report = render_report(self.state, self.reviews, self.repairs)
            try:
                self.store.write_text("final/report.md", report, replace=True)
                self.checkpoint()
            except (OSError, AgentError):
                self.on_event("[错误] 无法保存失败状态，请检查磁盘与权限。")
        return RunResult(self.state, self.store.root, report)
