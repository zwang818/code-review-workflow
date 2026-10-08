"""Render actual execution state; never turn syntax checks into runtime claims."""


def render_report(state, reviews: list, repairs: list) -> str:
    labels = {"no_issues_found": "本次静态审查未发现问题", "needs_attention": "仍需人工处理"}
    lines = ["# Workflow 执行报告", "",
             "> 离线固定样例演示，没有调用外部 LLM。" if state.demo else "> 真实模型模式；结论仍需人工核实。", "",
             "## 执行结果", "",
             "- 运行 ID：`%s`" % state.run_id,
             "- 程序状态：`%s`" % state.status,
             "- 结论：%s" % labels.get(state.outcome, "流程未正常完成"),
             "- 结束原因：%s" % (state.reason or "未记录"),
             "- 当前代码版本：%d" % state.code_version,
             "- 当前文件：`%s`" % (state.code_path or "尚未产生"),
             "- 语法检查：`%s`" % state.validation.get("syntax", "not_run"),
             "- 业务代码运行测试：`not_run`",
             "- 修复次数：%d / %d" % (state.fix_attempt, state.max_fix_attempts),
             "- 逻辑模型接口调用：%d；HTTP 额外重试：%d；实际工具调用：%d" %
             (state.model_calls, state.http_retries, state.tool_calls), "",
             "## 原始需求", "", state.requirement, "", "## 阶段记录", "",
             "| 阶段 | 状态 | 输入版本 | 耗时（秒） |", "|---|---|---:|---:|"]
    for stage in state.stages:
        lines.append("| %s | %s | %d | %.3f |" %
                     (stage["name"], stage["status"], stage["code_version"], stage.get("duration_seconds", 0)))
    if state.error:
        lines += ["", "## 错误", "", "- 阶段：`%s`" % state.error["stage"],
                  "- 类别：`%s`" % state.error["type"], "- 说明：" + state.error["message"]]
    lines += ["", "## 当前版本的审查问题", ""]
    if state.latest_review_path is None:
        lines.append("当前版本尚无完成的审查结果，不能给出无问题结论。")
    elif not state.issues:
        lines.append("本轮问题列表为空；仍应结合审查范围与流程结论判断。")
    for item in state.issues:
        lines += ["### %s [%s] %s" % (item["id"], item["severity"], item["title"]), "",
                  "- 位置：`%s:%d-%d`" % (state.code_path, item["line_start"], item["line_end"]),
                  "- 触发：" + item["trigger"], "- 影响：" + item["impact"],
                  "- 证据：", "```python", item["evidence"], "```",
                  "- 建议：" + item["suggestion"], ""]
    lines += ["", "## 审查与修复历史", ""]
    for review in reviews:
        lines.append("- R%03d：版本%d，源码范围 `%s`，问题%d项。" %
                     (review["round"], review["code_version"], review["coverage"], len(review["issues"])))
    for repair in repairs:
        lines.append("- F%03d：基于版本%d提交修改说明（不等于问题已验证解决）。" %
                     (repair["attempt"], repair["input_code_version"]))
        lines.extend("  - `%s`：%s；%s" % (c["issue_id"], c["action"], c["explanation"])
                     for c in repair["changes"])
    lines += ["", "## 验证范围与限制", "",
              "语法检查使用 ast.parse，没有执行或导入生成代码。静态审查可能遗漏或误报问题。",
              "无问题结论只适用于实际读取并审查的当前版本，不保证业务正确性。",
              "原始输入文件未被覆盖，代码快照统一保存为 UTF-8，编码声明会相应规范化。"]
    if reviews:
        lines.extend("- " + note for note in reviews[-1]["limitations"])
    return "\n".join(lines) + "\n"
