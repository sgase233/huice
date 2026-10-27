"""真实 DeepSeek API 的联调用例（可选）。

设置 ``HUICE_TEST_DEEPSEEK_API_KEY`` 才运行；``HUICE_TEST_DEEPSEEK_BASE_URL`` 与
``HUICE_TEST_DEEPSEEK_MODEL`` 可选（默认 ``https://api.deepseek.com`` / ``deepseek-chat``）。

除了客户端本身，这里还经 ``create_app`` 跑一次完整判断：验证模型只负责改写解释，
四态结论、清单与补问仍由 ``domain/rules.py`` 决定（AGENT_PROTOCOL §1 的边界）。

凭证处理与 ``test_obs_live.py`` 相同：Key 装在 ``Credentials`` 里（``repr`` 已重写），
带密钥的 ``Settings`` 只在辅助函数内临时构造，避免用例失败时被 pytest 打印出来。
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ApiProblem
from app.integrations.llm import MODEL_UNAVAILABLE_MESSAGE, DeepSeekClient
from app.main import create_app
from support import FakeRepository, published_draft

pytestmark = [pytest.mark.deepseek]

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REWRITE_WARNING = "解释由模型改写，结论以规则结果为准。"

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
AS_OF = "2026-10-27T10:00:00+08:00"

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}


@dataclass(frozen=True)
class Credentials:
    base_url: str
    model: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return f"Credentials(base_url={self.base_url!r}, model={self.model!r}, api_key=<redacted>)"


@pytest.fixture(scope="module")
def creds() -> Credentials:
    api_key = os.environ.get("HUICE_TEST_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        pytest.skip("未设置 HUICE_TEST_DEEPSEEK_API_KEY，跳过真实 DeepSeek 用例")
    return Credentials(
        base_url=os.environ.get("HUICE_TEST_DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip(),
        model=os.environ.get("HUICE_TEST_DEEPSEEK_MODEL", DEFAULT_MODEL).strip(),
        api_key=api_key,
    )


def _client(creds: Credentials, *, timeout: float) -> DeepSeekClient:
    return DeepSeekClient(
        base_url=creds.base_url,
        model=creds.model,
        api_key=creds.api_key,
        timeout=timeout,
    )


def _configured(settings: Settings, creds: Credentials) -> Settings:
    return dataclasses.replace(
        settings,
        model_mode="deepseek",
        deepseek_base_url=creds.base_url,
        deepseek_model=creds.model,
        deepseek_api_key=creds.api_key,
    )


def test_live_completion_returns_text_model_and_usage(creds) -> None:
    result = _client(creds, timeout=60).complete(
        system="你只输出两个汉字，不解释。",
        user="请用两个汉字回应：你好",
        max_tokens=16,
    )

    assert result.text.strip()
    assert result.model
    assert result.model == creds.model or result.model.startswith("deepseek")
    assert result.prompt_tokens >= 0
    assert result.completion_tokens >= 0
    assert result.elapsed_ms >= 0
    assert creds.api_key not in result.text


def test_invalid_key_becomes_503_without_leaking_the_key(creds) -> None:
    fake_key = "sk-invalid-" + uuid.uuid4().hex
    broken = DeepSeekClient(
        base_url=creds.base_url,
        model=creds.model,
        api_key=fake_key,
        timeout=30,
    )

    with pytest.raises(ApiProblem) as excinfo:
        broken.complete(system="只回答一个词。", user="你好", max_tokens=8)

    problem = excinfo.value
    assert problem.status_code == 503
    assert problem.code == "DEPENDENCY_UNAVAILABLE"
    assert problem.message == MODEL_UNAVAILABLE_MESSAGE
    rendered = repr(problem) + str(problem.details)
    assert fake_key not in rendered
    assert creds.base_url not in rendered


def test_assessment_lets_the_model_rewrite_only_the_explanation(settings, creds) -> None:
    application = create_app(_configured(settings, creds))
    repository = FakeRepository()
    application.state.repository = repository
    policy = published_draft(expressions=[INDUSTRY_GATE])
    repository.drafts.append(policy)
    client = TestClient(application)

    created = client.post(
        "/api/v1/enterprises",
        json={
            "nameSynthetic": "合成企业A",
            "period": YEAR,
            "facts": [
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": "F51", "unit": "code", "period": YEAR, "source": "user_input"},
            ],
        },
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/api/v1/assessment-runs",
        json={
            "enterpriseId": created.json()["id"],
            "policyIds": [str(policy.id)],
            "asOf": AS_OF,
            "period": YEAR,
        },
    )
    assert response.status_code == 200, response.text

    assessment = response.json()["assessments"][0]
    # 结论只由规则求值决定：行业被排除 → not_satisfied，模型无权改写。
    assert assessment["result"] == "not_satisfied"
    assert assessment["explanationMode"] == "deepseek"
    assert assessment["explanation"].strip()
    assert REWRITE_WARNING in assessment["warnings"]
    assert MODEL_UNAVAILABLE_MESSAGE not in assessment["warnings"]
    assert creds.api_key not in assessment["explanation"]
