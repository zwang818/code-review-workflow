# 验证记录

## 1. 环境与命令

2026年10月8日，macOS，Python 3.9.6：

```bash
python3 -m unittest discover -s tests -v
```

实际结果：**72项测试全部通过**。测试仅使用标准库，无需API密钥。HTTP测试在127.0.0.1随机端口启动临时服务，结束自动关闭，不访问外部模型。

## 2. 覆盖范围

| 测试文件 | 主要行为 |
|---|---|
| test_runner.py | 三角色通信、新版本复审、跳过、循环上限、无变化修复、全局预算、语法诊断、失败和取消保存、UTF-8快照、不执行源码、最终写入失败 |
| test_agents.py | 首次工具调用、角色隔离、结构纠正、问题ID匹配、工具预算配对、范围验证和摘要校验 |
| test_contracts.py | 严格JSON、精确字段、源码上限、版本、证据行范围、修复ID、上下文语法 |
| test_artifacts.py | 路径边界、禁止覆盖、原子替换失败保留旧快照、输出链接拒绝 |
| test_cli.py | 双入口、路径空格、原文件保留、退出码、无密钥、固定演示范围、quiet |
| test_llm.py | 实际本机HTTP三角色流程、普通请求不发送tools、工具结果配对、重试计数、401不重试、超时、重定向拒绝、响应大小与配置 |
| test_tools.py | 行号分页、截断、编码、AST、非法参数、路径及链接、文件大小、不执行目标代码 |

## 3. CLI验收

```bash
python3 -m workflow_agent run --demo --task-file examples/requirement.txt
python3 -m workflow_agent review --demo --root examples --file buggy_order.py --task-file examples/requirement.txt
python3 -m workflow_agent review --demo --root examples --file clean_order.py --task-file examples/requirement.txt
python3 -m workflow_agent review --demo --root examples --file buggy_order.py --task-file examples/requirement.txt --max-fixes 0
```

前三条预期退出0；最后一条预期退出3，并保留问题。需求入口产生2个代码版本，执行1次修复和2次审查。正常文件入口跳过生成和修复。

源码包解压后独立运行离线入口，核对Prompt与示例文件均已打包；不依赖week1目录。打包检查排除.env、runs、work及缓存。

## 4. 验证边界

- 离线模式是固定样例适配器，没有LLM推理，不支持任意需求。
- 本机HTTP测试是真实传输，但响应来自测试脚本，不能证明外部服务可用或模型结论准确。
- 未提供真实模型API配置，外部联调未完成。
- 自动检查解析和编译源码，不执行源码，业务测试标记not_run。
- 正常对照与反例是有限验收样例，不提供总体误报率或准确率保证。
- 自动恢复、并行、Web编排和多文件任务未实现。
