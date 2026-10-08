"""Environment configuration; no third-party dotenv dependency required."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines; existing environment variables take priority.

    Supports comments on their own lines and matching quotes around values.
    Intentionally does not execute shell expressions or expand variables.
    """
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ConfigError("无法读取 .env，请检查权限和 UTF-8 编码。") from exc
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            raise ConfigError(".env 第 %d 行应为 KEY=VALUE。" % number)
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigError(".env 第 %d 行的引号不匹配。" % number)
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout: float = 30.0
    max_retries: int = 2
    max_rounds: int = 10
    max_tool_calls: int = 24
    memory_turns: int = 4
    max_context_chars: int = 100000

    @classmethod
    def from_env(cls, require_api: bool = True) -> "Settings":
        try:
            settings = cls(
                api_key=os.getenv("OPENAI_API_KEY", "").strip(),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
                model=os.getenv("OPENAI_MODEL", "").strip(),
                timeout=float(os.getenv("AGENT_TIMEOUT", "30")),
                max_retries=int(os.getenv("AGENT_MAX_RETRIES", "2")),
                max_rounds=int(os.getenv("AGENT_MAX_ROUNDS", "10")),
                max_tool_calls=int(os.getenv("AGENT_MAX_TOOL_CALLS", "24")),
                memory_turns=int(os.getenv("AGENT_MEMORY_TURNS", "4")),
                max_context_chars=int(os.getenv("AGENT_MAX_CONTEXT_CHARS", "100000")),
            )
        except ValueError as exc:
            raise ConfigError("AGENT_* 数值配置格式错误，请参考 .env.example。") from exc
        settings.validate(require_api)
        return settings

    def validate(self, require_api: bool = True) -> None:
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise ConfigError("AGENT_TIMEOUT 必须在 0 到 300 秒之间。")
        for label, value, low, high in (
            ("MAX_RETRIES", self.max_retries, 0, 5),
            ("MAX_ROUNDS", self.max_rounds, 1, 30),
            ("MAX_TOOL_CALLS", self.max_tool_calls, 1, 100),
            ("MEMORY_TURNS", self.memory_turns, 1, 20),
            ("MAX_CONTEXT_CHARS", self.max_context_chars, 4000, 500000),
        ):
            if not low <= value <= high:
                raise ConfigError("AGENT_%s 必须在 %d 到 %d 之间。" % (label, low, high))
        if not require_api:
            return
        if not self.api_key or self.api_key == "your-api-key":
            raise ConfigError("请在 .env 中配置 OPENAI_API_KEY；无密钥可先使用 --demo。")
        if not self.model or self.model == "your-tool-capable-model":
            raise ConfigError("请将 OPENAI_MODEL 设为服务商提供的、支持 tools 的模型名称。")
        try:
            url = urlsplit(self.base_url)
            host = url.hostname
            _ = url.port
        except ValueError as exc:
            raise ConfigError("OPENAI_BASE_URL 格式无效。") from exc
        local_http = url.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")
        if (url.scheme != "https" and not local_http) or not host:
            raise ConfigError("OPENAI_BASE_URL 需要 HTTPS；仅本机调试允许 HTTP。")
        if url.username or url.password or url.query or url.fragment:
            raise ConfigError("OPENAI_BASE_URL 不应包含凭据、查询参数或片段。")


@dataclass(frozen=True)
class WorkflowSettings:
    model: Settings = field(default_factory=Settings)
    max_fixes: int = 2
    max_model_calls: int = 40
    output_dir: str = "runs"

    @classmethod
    def from_env(cls, require_api: bool = True) -> "WorkflowSettings":
        model = Settings.from_env(require_api=require_api)
        try:
            result = cls(model=model, max_fixes=int(os.getenv("WORKFLOW_MAX_FIXES", "2")),
                         max_model_calls=int(os.getenv("WORKFLOW_MAX_MODEL_CALLS", "40")),
                         output_dir=os.getenv("WORKFLOW_OUTPUT_DIR", "runs"))
        except ValueError as exc:
            raise ConfigError("WORKFLOW_* 数值配置必须为整数。") from exc
        result.validate()
        return result

    def validate(self) -> None:
        if type(self.max_fixes) is not int or not 0 <= self.max_fixes <= 3:
            raise ConfigError("修复次数必须在0到3之间。")
        if type(self.max_model_calls) is not int or not 1 <= self.max_model_calls <= 200:
            raise ConfigError("全局模型调用预算必须在1到200之间。")
        if not self.output_dir.strip():
            raise ConfigError("输出目录不能为空。")
