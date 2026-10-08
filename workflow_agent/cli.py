"""Two input modes; deterministic report and explicit exit statuses."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import WorkflowSettings, load_dotenv
from .contracts import code_text, text
from .demo import DemoClient
from .errors import AgentError
from .llm import ChatClient
from .runner import WorkflowRunner
from .tools import ToolRegistry


def read_requirement(path: str) -> str:
    try:
        with Path(path).expanduser().open("rb") as stream:
            raw = stream.read(48001)
        if len(raw) > 48000:
            raise AgentError("需求文件超过48000字节，请缩短内容。")
        return text(raw.decode("utf-8-sig"), "需求", 12000)
    except (OSError, UnicodeError) as exc:
        raise AgentError("无法读取需求文件，请检查路径、权限和 UTF-8 编码。") from exc


def build_parser():
    parser = argparse.ArgumentParser(description="三个角色协作的 Python 编码、审查与修复 Workflow")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    for name in ("run", "review"):
        cmd = commands.add_parser(name, help="从需求生成并审查" if name == "run" else "审查并修复已有文件")
        task = cmd.add_mutually_exclusive_group(required=True)
        task.add_argument("--task", help="直接输入需求")
        task.add_argument("--task-file", help="UTF-8 需求文件")
        cmd.add_argument("--demo", action="store_true", help="离线固定样例，不调用外部 LLM")
        cmd.add_argument("--env-file", default=".env", help="配置文件，默认当前目录 .env")
        cmd.add_argument("--max-fixes", type=int, help="最多修复0到3次，默认2")
        cmd.add_argument("--max-model-calls", type=int, help="全局逻辑模型调用预算1到200，默认40")
        cmd.add_argument("--output-dir", help="产物根目录，默认 runs")
        cmd.add_argument("--quiet", action="store_true", help="隐藏进度日志，仅输出最终报告")
        if name == "review":
            cmd.add_argument("--root", default=".", help="输入根目录")
            cmd.add_argument("--file", required=True, help="相对 --root 的 .py 文件")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        requirement = read_requirement(args.task_file) if args.task_file else text(args.task, "需求", 12000)
        source = None
        if args.command == "review":
            _, source = ToolRegistry(Path(args.root).expanduser())._source(args.file)
            source = code_text(source, generated=False)
        load_dotenv(Path(args.env_file).expanduser())
        settings = WorkflowSettings.from_env(require_api=not args.demo)
        overrides = {key: getattr(args, key) for key in ("max_fixes", "max_model_calls", "output_dir")
                     if getattr(args, key) is not None}
        settings = replace(settings, **overrides)
        settings.validate()
        if args.demo:
            DemoClient.validate_input(requirement, source)
        client = DemoClient() if args.demo else ChatClient(settings.model)

        def progress(message):
            if not args.quiet:
                print(message, file=sys.stderr, flush=True)

        progress("[启动] " + ("离线固定样例演示，没有调用外部 LLM。" if args.demo else "真实模型模式：" + settings.model.model))
        result = WorkflowRunner(client, settings, args.demo, progress).run(requirement, source)
        print(result.report, end="")
        progress("[产物] " + str(result.run_dir))
        return result.exit_code
    except (AgentError, OSError, RuntimeError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
