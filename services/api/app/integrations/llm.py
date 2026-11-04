"""模型适配：DeepSeek（OpenAI 兼容 ``/chat/completions``）与 ``fixture`` 两种模式。

设计约束（沿用原 MaaS 适配器的边界）：

* 模型**只负责措辞与抽取建议**，不决定资格判断——判断结果一律由 ``domain/rules.py``
  求值，模型调用失败不影响已审核规则的结论。
* 任何失败都转成契约里的错误码：限流 ``429 UPSTREAM_RATE_LIMITED``，其余
  ``503 DEPENDENCY_UNAVAILABLE``；消息里不含 base URL、模型名或密钥。
* 失败另带两个不进响应体的内部标记：``retryable``（429、超时/连接、5xx、
  坏响应可重试；上游 4xx 不可重试，多半是密钥或模型名写错）与
  ``usage_unknown``（上游可能已处理并计费，但我们拿不到用量）。
* ``fixture`` 模式不联网、不读密钥，测试与演示默认走这条路径。

只使用标准库 ``urllib``，不为一次 HTTP 调用再引入运行期依赖。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..config import Settings
from ..errors import ApiProblem

MODEL_UNAVAILABLE_MESSAGE = "模型服务暂不可用，已改用规则结果与已存说明。"
MODEL_RATE_LIMITED_MESSAGE = "模型服务调用被限流，请稍后重试。"

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class LlmResult:
    """一次模型调用的结果，字段与契约 ``ModelUsage`` 对齐。"""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int


class LlmClient(Protocol):
    mode: str
    #: 实际请求的模型名；``fixture`` 下为 ``"fixture"``。
    model_name: str

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult: ...


def _problem(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool,
    usage_unknown: bool = False,
) -> ApiProblem:
    """带内部标记的模型故障：标记只用于记轨迹与日志，不改响应形状。"""

    return ApiProblem(
        status_code, code, message, retryable=retryable, usage_unknown=usage_unknown
    )


class FixtureLlmClient:
    """离线替身：返回固定文本，token 记 0，供测试与无网演示使用。"""

    mode = "fixture"
    model_name = "fixture"

    def __init__(self, text: str = "（示例输出：当前为 fixture 模式，未调用外部模型。）") -> None:
        self._text = text

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        return LlmResult(
            text=self._text,
            model="fixture",
            prompt_tokens=0,
            completion_tokens=0,
            elapsed_ms=0,
        )


# 返回 (HTTP 状态码, 响应体字节)。抽出成可注入的传输层，测试不必联网。
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


def _http_post(url: str, payload: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也会走到这里
        return int(exc.code), exc.read()


class DeepSeekClient:
    """DeepSeek 官方 API 的最小客户端（OpenAI 兼容协议）。"""

    mode = "deepseek"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Transport = _http_post,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "huice-api/1.0",
        }

        started = time.monotonic()
        try:
            status, raw = self._transport(self._url, body, headers, self._timeout)
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            # 请求可能已经到达上游：用量未知，重试有意义。
            raise _problem(503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                           retryable=True, usage_unknown=True) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if status == 429:
            # 上游明确拒绝，没有产生 token；过一会儿再来是合理的。
            raise _problem(429, "UPSTREAM_RATE_LIMITED", MODEL_RATE_LIMITED_MESSAGE,
                           retryable=True)
        if not 200 <= status < 300:
            # 4xx（鉴权、参数、模型名写错）重试也不会成功；5xx 与分析失败同属可重试。
            client_error = 400 <= status < 500
            raise _problem(503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                           retryable=not client_error, usage_unknown=not client_error)

        data = self._parse(raw)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # 拿到了 2xx 却没拿到答案：上游很可能已经计费。
            raise _problem(503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                           retryable=True, usage_unknown=True) from exc

        usage = data.get("usage") or {}
        return LlmResult(
            text=str(text or ""),
            model=str(data.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _problem(503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                           retryable=True, usage_unknown=True) from exc
        if not isinstance(data, dict):
            raise _problem(503, "DEPENDENCY_UNAVAILABLE", MODEL_UNAVAILABLE_MESSAGE,
                           retryable=True, usage_unknown=True)
        return data


def build_llm_client(settings: Settings) -> LlmClient:
    if settings.model_mode == "deepseek":
        return DeepSeekClient(
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
        )
    return FixtureLlmClient()
