# 演示步骤

## 1. 完整三角色流程

```bash
python3 -m workflow_agent run --demo --task-file examples/requirement.txt
```

先明确说明“这是离线固定样例，未调用LLM”。观察generate、review、repair及再次review，最后打开输出目录：

- code/v001.py和code/v002.py：初版与修复版。
- reviews/r001.json和reviews/r002.json：问题如何变化。
- repairs/f001.json：如何引用上游问题ID。
- state.json和events.jsonl：版本、状态、预算和结束原因。
- final/report.md：当前结论及验证限制。

## 2. 条件分支

```bash
python3 -m workflow_agent review --demo --root examples --file clean_order.py --task-file examples/requirement.txt
```

展示正常文件没有问题时跳过修复，避免为展示协作而编造缺陷。

## 3. 预算停止

```bash
python3 -m workflow_agent review --demo --root examples --file buggy_order.py --task-file examples/requirement.txt --max-fixes 0
```

展示需人工处理的结果及退出码3，说明流程正常结束不一定代表问题已解决。

## 4. 真实模型演示

填写.env后去掉--demo，运行同样任务。不要在演示中展示密钥。模型可能直接生成正确实现并跳过修复，这是合法行为；已有缺陷文件入口更适合展示审查与修复通信。

真实运行时检查模型确实使用工具读取当前版本，并核对报告行号与代码。真实模型响应耗时取决于服务，未完成联调前不能把离线结果作为真实运行证据。

临时作业文件没有规定视频是否必交及长度，按老师后续正式通知确定。
