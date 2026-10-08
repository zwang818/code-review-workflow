"""Minimal OpenAI-compatible Chat Completions transport (stdlib only)."""

import http.client
import json
import socket
import time
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Settings
from .errors import ModelError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward bearer credentials to another origin after a redirect.
        return None


def normalize_message(response: dict) -> dict:
    """Validate the protocol envelope; invalid function arguments stay recoverable."""
    try:
        choice = response["choices"][0]
        raw = choice["message"]
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ModelError("模型输出被截断或过滤，未将其视为完整审查。请缩小范围或检查模型配置。")
        if raw.get("role") != "assistant":
            raise ValueError("role")
        content = raw.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("content")
        calls = raw.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 16:
            raise ValueError("tool_calls")
        result = {"role": "assistant", "content": content}
        clean_calls, identifiers = [], set()
        for call in calls:
            fn = call["function"]
            if (call.get("type") != "function" or not isinstance(call.get("id"), str)
                    or not call["id"] or call["id"] in identifiers
                    or not isinstance(fn.get("name"), str) or not fn["name"]
                    or not isinstance(fn.get("arguments"), str)):
                raise ValueError("function call")
            identifiers.add(call["id"])
            clean_calls.append({"id": call["id"], "type": "function",
                                "function": {"name": fn["name"], "arguments": fn["arguments"]}})
        if clean_calls:
            result["tool_calls"] = clean_calls
        elif not content or not content.strip():
            raise ModelError("模型没有返回报告或工具调用，请检查模型是否支持 tools。")
        return result
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise ModelError("模型响应不符合 Chat Completions 工具调用格式。") from exc


class ChatClient:
    def __init__(self, settings: Settings, on_retry: Optional[Callable[[str], None]] = None):
        settings.validate(require_api=True)
        self.settings = settings
        self.on_retry = on_retry or (lambda message: None)
        self.opener = build_opener(NoRedirect())

    def complete(self, messages: list, tools: list, tool_choice: str = "auto") -> dict:
        payload = {"model": self.settings.model, "messages": messages, "stream": False}
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice)
        request = Request(
            self.settings.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.settings.api_key,
                     "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        for attempt in range(self.settings.max_retries + 1):
            try:
                with self.opener.open(request, timeout=self.settings.timeout) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ModelError("模型响应超过 2 MiB 限制。")
                try:
                    decoded = json.loads(body)
                except (ValueError, UnicodeError) as exc:
                    raise ModelError("服务返回了无效 JSON，请检查 BASE_URL 是否为 API 地址。") from exc
                return normalize_message(decoded)
            except HTTPError as exc:
                status = exc.code
                exc.close()
                retryable = status == 429 or 500 <= status < 600
                hints = {400: "请检查模型对 tools 和 tool_choice 的支持。",
                         401: "请检查 API 密钥。", 403: "请检查账号和模型权限。",
                         404: "请检查 BASE_URL 和模型名称。",
                         429: "请求受限或额度不足。"}
                error = "API HTTP %d：%s" % (status, hints.get(status, "服务请求失败。"))
                # Deliberately do not echo service bodies, URLs or credentials.
                if not retryable or attempt == self.settings.max_retries:
                    raise ModelError(error) from exc
            except (URLError, socket.timeout, TimeoutError, http.client.HTTPException, OSError) as exc:
                error = "API 网络连接失败或超时，请检查网络、代理及服务地址。"
                if attempt == self.settings.max_retries:
                    raise ModelError(error) from exc
            delay = min(2 ** attempt, 8)
            self.on_retry("%s %d秒后重试（%d/%d）。" %
                          (error, delay, attempt + 1, self.settings.max_retries))
            time.sleep(delay)
        raise ModelError("模型调用失败。")
