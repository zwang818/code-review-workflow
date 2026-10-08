# 设计与实现说明

## 1. 职责边界

三个角色负责生成、审查和修复；Runner是确定性调度程序。各角色使用自己的Prompt和每次调用新建的消息上下文，底层共享可替换客户端。

`NodeContext` 提供需求、当前源码/路径、版本、摘要、语法检查、问题、历史审查和最近修复；`NodeResult` 返回节点名称和经过校验的数据。审查节点初始上下文不提供当前源码，必须通过工具读取，程序核对读取覆盖。

Agent不直接写共享状态。Runner校验产物、分配问题ID、决定下一阶段并管理版本。

## 2. 节点输出契约

### 编码

```json
{
  "code": "def answer():\n    return 42\n",
  "summary": "实现一个返回常量的函数。",
  "assumptions": []
}
```

### 审查

```json
{
  "code_version": 1,
  "coverage": "complete",
  "scope_note": "完整读取当前源码，进行静态分析。",
  "issues": [
    {
      "line_start": 1,
      "line_end": 1,
      "severity": "medium",
      "title": "可变默认参数",
      "evidence": "def add_order(item, orders=[]):",
      "trigger": "省略参数并重复调用时。",
      "impact": "不同调用可能共享列表。",
      "suggestion": "默认值改为None，在函数体创建列表。"
    }
  ],
  "limitations": ["没有运行代码。"]
}
```

行号和证据必须匹配上下文对应源码。代码版本必须一致。程序根据实际读取行范围和AST调用核实coverage；模型自报complete但证据不足时会降级partial。coverage只描述源码获取范围。

程序随后加入摘要、工具计数、审查轮次和 `R001-I001` 等问题ID。模型不能自行输出这些额外字段。上述示例说明契约形式，不是已经执行的特定文件审查结果。

### 修复

```json
{
  "code": "def add_order(item, orders=None):\n    result = [] if orders is None else list(orders)\n    result.append(item)\n    return result\n",
  "changes": [
    {
      "issue_id": "R001-I001",
      "action": "changed",
      "explanation": "默认值改为None，并复制传入列表。"
    }
  ],
  "remaining_notes": ["尚未运行行为测试。"]
}
```

changes恰好覆盖输入问题ID，action只能changed或not_changed。程序验证引用，不把修复声明当作独立审查结论。

所有输出必须为严格JSON对象，拒绝围栏、重复键、非有限数值、缺失/额外字段以及超限源码。验证失败仅允许一次同节点纠正，不重启整个Workflow。

## 3. 实际流程

```text
需求模式：generate → 保存v001
文件模式：读取输入 → 保存v001，跳过generate

循环：
  validate 当前版本
  review 当前版本
  若范围partial：needs_attention
  若语法passed且无问题：no_issues_found，跳过repair
  若修复次数已耗尽：needs_attention
  repair 当前版本及当前问题
  若源码摘要不变：needs_attention
  保存新版本，进入下一轮validate与review
```

默认最多两次修复，最多三次审查。语法失败时Runner将确定性语法诊断放入问题列表，防止模型漏报导致误通过。

逻辑调用预算由MeteredClient统一管理，包括生成、审查、修复、结构纠正和节点收束。HTTP传输重试单独计数。预算耗尽时不再调用模型，直接基于已有状态生成部分报告。

## 4. 审查节点内部工具循环

第一轮 `tool_choice=required`，随后模型可继续调用或输出JSON；达到节点轮数或工具次数上限时禁止工具并收束。批次超限的调用仍获得对应tool_call_id错误结果，保持协议完整。

工具仅限read_file和analyze_python，且路径必须指向当前版本。每次工具读取前核对源码摘要，节点返回后Runner再次核对文件。跨版本、其他文件、隐藏路径和目录外链接被拒绝。

读取按真实行号分页。仅实际完整返回的行计入覆盖；超长行截断不计入完整覆盖。AST调用与完整行覆盖同时成立才允许complete。

系统Prompt将源码、注释、文件名和历史修复说明作为数据。程序限制权限和资源，不能保证模型对数据中的诱导文字始终判断正确。

## 5. 状态和产物一致性

WorkflowState集中记录执行状态、结论、当前版本、源码摘要、审查和修复轮次、计数及错误。`status=completed`与`outcome=needs_attention`可以同时出现。

ArtifactStore创建独立UUID目录，以UTF-8写入版本化源码；原输入的换行和编码声明规范化。新文件写成功后才更新当前版本；旧版本不覆盖。产生新版本时清空旧的当前审查引用和语法结果，避免新版本未审查就被旧报告判定通过。

JSON快照通过临时文件、flush/fsync和同目录os.replace保存。旧检查点在替换失败时保留。日志为追加JSONL，UTC时间带时区。

进程取消或节点失败保存阶段与已完成产物；最终文件保存失败会覆盖原成功状态，不能仍返回成功。严重磁盘故障下可能无法保存失败快照，控制台会明确提示。

唯一运行目录和路径检查适合单人本地课程项目，并不是抵御恶意并发文件修改的操作系统沙箱。首版不自动恢复检查点。

## 6. 语法、运行与审查结论

SyntaxValidator使用ast.parse和compile检查上下文语法，例如顶层return也会失败。编译得到的代码对象不会执行。业务代码运行状态始终not_run。

AST规则来自week1，提供可变默认值、异常处理、动态执行和字面量除零等有限线索。它们不覆盖所有业务逻辑，语义审查由真实模型完成。源码证据匹配只能排除明显错位，不能证明问题判断正确。

最终报告由普通程序根据真实状态和历史产物生成，不再请模型总结，因此全局预算耗尽后仍可输出已有结果和限制。

## 7. 扩展方式

统一接口为 `run(NodeContext) -> NodeResult`。扩展时增加独立角色、对应Prompt与校验，再在Runner显式注册及连接，不需改模型传输层。

后续可增加检查点恢复、独立执行环境中的行为测试、多文件支持或可视化展示。当前固定顺序与有限循环已满足课程MVP，不引入通用调度引擎。
