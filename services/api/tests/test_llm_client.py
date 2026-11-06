"""DeepSeek 模型客户端：请求形状、成功解析、失败映射与密钥不外泄。"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.config import ConfigError, Settings
from app.errors import ApiProblem
from app.integrations.llm import (
    DeepSeekClient,
    FixtureLlmClient,
    build_llm_client,
)

API_KEY = "sk-test-0123456789"
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"


def make_settings(**overrides) -> Settings:
    base = {
        "APP_ENV": "local",
        "API_HOST": "127.0.0.1",
        "DATABASE_URL": "postgresql://huice:pw@127.0.0.1:5432/huice",
        "CORS_ORIGINS": "http://127.0.0.1:5173",
        "MODEL_MODE": "deepseek",
        "ADMIN_TOKEN": "0123456789abcdef0123456789abcdef",
        "DEMO_ACCESS_MODE": "local",
        "DEEPSEEK_BASE_URL": BASE_URL,
        "DEEPSEEK_MODEL": MODEL,
        "DEEPSEEK_API_KEY": API_KEY,
    }
    base.update(overrides)
    return Settings.from_env(base)


class RecordingTransport:
    """记录请求并返回预设响应的假传输层。"""

    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self.body = body
        self.calls: list[dict] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "payload": json.loads(payload.decode("utf-8")),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.status, self.body


def ok_body(text: str = "建议条款文案") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 17, "total_tokens": 48},
        },
        ensure_ascii=False,
    ).encode("utf-8")


def client(transport: RecordingTransport, **kwargs) -> DeepSeekClient:
    return DeepSeekClient(
        base_url=kwargs.pop("base_url", BASE_URL),
        model=kwargs.pop("model", MODEL),
        api_key=kwargs.pop("api_key", API_KEY),
        transport=transport,
        **kwargs,
    )


def test_fixture_client_returns_offline_text():
    result = FixtureLlmClient("固定文案").complete(system="s", user="u")
    assert result.text == "固定文案"
    assert result.model == "fixture"
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


def test_request_hits_chat_completions_with_bearer_key():
    transport = RecordingTransport(body=ok_body())
    client(transport).complete(system="你是政策助手", user="抽取条件")

    (call,) = transport.calls
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["payload"]["model"] == MODEL
    assert call["payload"]["stream"] is False
    assert call["payload"]["messages"] == [
        {"role": "system", "content": "你是政策助手"},
        {"role": "user", "content": "抽取条件"},
    ]


def test_base_url_trailing_slash_is_trimmed():
    transport = RecordingTransport(body=ok_body())
    client(transport, base_url="https://api.deepseek.com/v1/").complete(system="s", user="u")
    assert transport.calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_success_returns_text_and_usage():
    transport = RecordingTransport(body=ok_body("请补充近三年纳税证明。"))
    result = client(transport).complete(system="s", user="u", max_tokens=256)

    assert result.text == "请补充近三年纳税证明。"
    assert result.model == "deepseek-chat"
    assert result.prompt_tokens == 31
    assert result.completion_tokens == 17
    assert result.elapsed_ms >= 0
    assert result.usage_known
    assert transport.calls[0]["payload"]["max_tokens"] == 256


def test_rate_limit_maps_to_429():
    transport = RecordingTransport(status=429, body=b"{}")
    with pytest.raises(ApiProblem) as info:
        client(transport).complete(system="s", user="u")
    assert info.value.status_code == 429
    assert info.value.code == "UPSTREAM_RATE_LIMITED"
    # 限流可以过一会儿重试；但状态码证明不了上游没处理这次请求，用量记未知。
    assert info.value.retryable
    assert info.value.usage_unknown


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
def test_other_failures_map_to_503_without_leaking_secrets(status):
    transport = RecordingTransport(status=status, body=b'{"error":{"message":"bad"}}')
    with pytest.raises(ApiProblem) as info:
        client(transport).complete(system="s", user="u")

    problem = info.value
    assert problem.status_code == 503
    assert problem.code == "DEPENDENCY_UNAVAILABLE"
    text = str(problem) + json.dumps(problem.details, ensure_ascii=False)
    assert API_KEY not in text
    assert BASE_URL not in text
    assert MODEL not in text
    # 上游 4xx（密钥/模型名/参数写错）重试也不会成功，应记 fatal_error；
    # 5xx 属于可重试。两类都无法从客户端证明上游没计费，用量一律记未知。
    assert problem.retryable is (status >= 500)
    assert problem.usage_unknown


def test_malformed_json_maps_to_503():
    transport = RecordingTransport(body=b"not-json")
    with pytest.raises(ApiProblem) as info:
        client(transport).complete(system="s", user="u")
    assert info.value.status_code == 503
    # 2xx 却解析不出结果：上游很可能已经把这次调用算了钱，用量记未知。
    assert info.value.retryable and info.value.usage_unknown


def test_missing_choices_maps_to_503():
    transport = RecordingTransport(body=b'{"model":"deepseek-chat"}')
    with pytest.raises(ApiProblem) as info:
        client(transport).complete(system="s", user="u")
    assert info.value.code == "DEPENDENCY_UNAVAILABLE"


def test_network_error_maps_to_503():
    def boom(url, payload, headers, timeout):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(ApiProblem) as info:
        DeepSeekClient(
            base_url=BASE_URL, model=MODEL, api_key=API_KEY, transport=boom
        ).complete(system="s", user="u")
    assert info.value.status_code == 503
    assert "connection refused" not in str(info.value)
    # 连接中断时请求可能已经到了上游：可重试，但用量未知。
    assert info.value.retryable and info.value.usage_unknown


def test_missing_usage_is_recorded_as_unknown():
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    result = client(RecordingTransport(body=body)).complete(system="s", user="u")
    assert result.text == "ok"
    # 2xx 却没有 usage：可能已计费，0 只是占位，必须标记未知。
    assert not result.usage_known
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.model == MODEL


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 12},
        {"completion_tokens": 7},
        {"prompt_tokens": "十二", "completion_tokens": 7},
        {"prompt_tokens": 12, "completion_tokens": -1},
        {"prompt_tokens": True, "completion_tokens": 7},
        {"prompt_tokens": 12.5, "completion_tokens": 7},
    ],
)
def test_partial_or_malformed_usage_is_unknown_not_zero(usage):
    body = json.dumps({"choices": [{"message": {"content": "ok"}}], "usage": usage}).encode("utf-8")
    result = client(RecordingTransport(body=body)).complete(system="s", user="u")
    assert result.text == "ok"
    assert not result.usage_known
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)


def test_digit_string_usage_is_accepted():
    body = json.dumps(
        {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": "31", "completion_tokens": 17}}
    ).encode("utf-8")
    result = client(RecordingTransport(body=body)).complete(system="s", user="u")
    assert result.usage_known
    assert (result.prompt_tokens, result.completion_tokens) == (31, 17)


def test_build_llm_client_dispatches_by_mode():
    fixture = build_llm_client(make_settings(MODEL_MODE="fixture"))
    assert isinstance(fixture, FixtureLlmClient)
    assert fixture.mode == "fixture"

    remote = build_llm_client(make_settings())
    assert isinstance(remote, DeepSeekClient)
    assert remote.mode == "deepseek"


def test_deepseek_mode_without_key_fails_before_any_client_is_built():
    with pytest.raises(ConfigError) as info:
        make_settings(DEEPSEEK_API_KEY="")
    assert "DEEPSEEK_API_KEY" in str(info.value)
