"""E2 草案的独立校验：结构（contracts/check_e2_draft.py）+ 与现行实现的对照。

分工与 E1 一致：``contracts/openapi.e2-draft.yaml`` 说「应该是什么样」，``services/api``
是事实。能机器判的部分（操作清单、发布出去的 security 与 x-e1-gate、组件形状、运行时
常量、错误码是否真被抛出）在这里判；不能机器判的部分（候选该不该自动提交、审核员的
工作流）由草案正文与 PR 里的评审表负责。

E2 的 8 个操作**已实现但未并入** ``openapi.yaml``——所以这里既断言「草案与实现一致」，
也断言「草案还没进正式契约」，避免一份没评审完的接口悄悄变成对外承诺。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.discovery import fetch as fetch_module
from app.discovery import runner as runner_module
from app.main import API_PREFIX, create_app
from app.routes import discovery as discovery_routes
from app.schemas import CandidateStatus, DiscoveryTerminalReason
from support import ADMIN_TOKEN, DEMO_SESSION_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
DRAFT_PATH = CONTRACTS / "openapi.e2-draft.yaml"
BASE_PATH = CONTRACTS / "openapi.yaml"

#: 草案自己定义、且实现里也发布成同名组件的 schema。逐名列出而不是按前缀筛：
#: ``DiscardCandidateInput`` 不在 Discovery/Candidate 前缀里，按前缀会漏掉它。
E2_COMPONENTS = {
    "DiscoveryLimits",
    "DiscoverySettings",
    "DiscoverySettingsUpdate",
    "DiscoverySourceStatus",
    "DiscoveryRun",
    "DiscoveryRunList",
    "CandidateMetadata",
    "CandidateSummary",
    "CandidateList",
    "CandidateDetail",
    "CandidateUpdate",
    "DiscardCandidateInput",
}


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_e2_draft", CONTRACTS / "check_e2_draft.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def draft() -> dict:
    return yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def served() -> dict:
    application = create_app(
        Settings(
            app_env="local",
            api_host="127.0.0.1",
            database_url="postgresql://huice:huice@127.0.0.1:5432/huice_test",
            admin_token=ADMIN_TOKEN,
            demo_access_mode="local",
            demo_session_secret=DEMO_SESSION_SECRET,
            local_object_dir=".local-objects-test",
        )
    )
    return application.openapi()


def test_draft_validator_passes():
    counts, errors = _load_checker().check()
    assert errors == [], "草案校验未通过：\n" + "\n".join(errors)
    assert counts["operations"] == 8
    assert counts["error_codes"] >= 10


def test_draft_operations_are_not_in_the_published_contract(draft):
    """草案是评审中的东西：并入前不能出现在 openapi.yaml 里。

    并入动作（改 openapi.yaml + 升版本 + 重新生成前端类型）单独走一次评审，
    这里只是防止「代码写完了顺手把契约也改了」。
    """

    base = yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))
    for path in draft["paths"]:
        assert path not in base["paths"], f"{path} 已经进了正式契约，应从草案移除"


def test_served_operations_match_the_draft(draft, served):
    """服务端实际挂载的操作与草案一一对应（operationId、security、x-e1-gate）。"""

    for path, operations in draft["paths"].items():
        for method, declared in operations.items():
            actual = served["paths"][f"{API_PREFIX}{path}"][method]
            assert actual["operationId"] == declared["operationId"], path
            assert actual["security"] == declared["security"], path
            for mode in ("local", "restricted"):
                assert (
                    actual["x-e1-gate"][mode] == declared["x-e1-gate"][mode]
                ), (path, mode)


def test_component_shapes_match_the_served_schemas(draft, served):
    """草案里 E2 自己的组件必须与服务端发布的同名 schema 同形。

    E1 的复查暴露过一类问题：草案把字段写成可选、实现却是必填（或反过来），前端照
    草案生成的表单会永远提交不过。这里比属性名与必填集合；取值约束（枚举、上下界）
    由 ``check_e2_draft.py`` 的样例校验与运行时常量用例覆盖。
    """

    served_schemas = served["components"]["schemas"]
    draft_schemas = draft["components"]["schemas"]

    for name in sorted(E2_COMPONENTS):
        assert name in served_schemas, f"服务端没有发布组件 {name}"
        assert name in draft_schemas, f"草案缺少组件 {name}"
        declared = draft_schemas[name]
        actual = served_schemas[name]
        assert set(declared.get("properties") or {}) == set(
            actual.get("properties") or {}
        ), f"{name} 的属性集合与实现不一致"
        assert set(declared.get("required") or []) == set(
            actual.get("required") or []
        ), f"{name} 的必填集合与实现不一致"

    # 草案多出来的组件只允许是 CandidateStatus（实现里它是查询参数的 inline enum）。
    extras = set(draft_schemas) - E2_COMPONENTS
    assert extras == {"CandidateStatus"}, f"草案里有未对照的组件：{sorted(extras)}"


def test_candidate_status_and_terminal_reasons_match_the_draft(draft):
    """状态枚举是封闭集合：草案、schema、运行时模型三处必须一致。"""

    model = draft["x-e2-runtime-model"]
    declared_states = set(model["candidate-lifecycle"]["states"])
    assert declared_states == set(CandidateStatus.__args__)
    assert declared_states == set(draft["components"]["schemas"]["CandidateStatus"]["enum"])

    declared_reasons = set(model["terminal-reasons"])
    assert declared_reasons == set(DiscoveryTerminalReason.__args__)
    run_schema = draft["components"]["schemas"]["DiscoveryRun"]
    run_reason = run_schema["properties"]["terminalReason"]["anyOf"][0]["enum"]
    assert set(run_reason) == declared_reasons

    # 可编辑态与路由里的 _EDITABLE 同源，改一处不改另一处就会转红。
    assert set(model["candidate-lifecycle"]["editable"]) == set(
        discovery_routes._EDITABLE
    )


def test_runtime_limits_match_the_code(draft):
    """草案里的运行时常量必须真的等于代码常量，否则文档说的上限就不是实际上限。

    这些数字决定了「一次抓多少、多久放弃、烧多少 token」，写错一个数量级不会让任何
    接口报错，只会让审核员在后台看到一份假的承诺。
    """

    model = draft["x-e2-runtime-model"]
    guard = model["fetch-guards"]

    assert guard["max-body-bytes"] == fetch_module.MAX_BODY_BYTES
    assert guard["per-request-timeout-seconds"] == fetch_module.REQUEST_TIMEOUT_SECONDS
    assert guard["max-redirects"] == fetch_module.MAX_REDIRECTS
    assert guard["max-new-candidates-per-run"] == fetch_module.MAX_CANDIDATES_PER_RUN
    assert guard["min-host-interval-seconds"] == fetch_module.MIN_HOST_INTERVAL_SECONDS
    assert guard["allowed-hosts"] == "、".join(fetch_module.OFFICIAL_HOST_SUFFIXES) + " 及其子域"

    assert model["token-budget"]["run"] == runner_module.RUN_TOKEN_BUDGET
    assert model["token-budget"]["per-candidate"] == runner_module.CANDIDATE_TOKEN_BUDGET
    assert model["token-budget"]["completion-reserve"] == runner_module.MAX_COMPLETION_TOKENS
    assert model["token-budget"]["prompt-overhead"] == runner_module.PROMPT_OVERHEAD_TOKENS
    assert model["run-deadline"].strip().startswith(
        f"单轮墙钟上限 {runner_module.MAX_RUN_SECONDS // 60} 分钟"
    )

    interval = model["interval"]
    assert interval["min-minutes"] == discovery_routes.MIN_INTERVAL_MINUTES
    assert interval["max-minutes"] == discovery_routes.MAX_INTERVAL_MINUTES
    limits = draft["components"]["schemas"]["DiscoverySettings"]["properties"]
    assert limits["intervalMinutes"]["minimum"] == discovery_routes.MIN_INTERVAL_MINUTES
    assert limits["intervalMinutes"]["maximum"] == discovery_routes.MAX_INTERVAL_MINUTES


def test_every_declared_error_code_is_actually_raised(draft):
    """草案登记的错误码必须在服务端代码里真的出现过。

    反过来（代码里抛了、表里没写）没法全自动判——错误码在多个模块里以字面量出现，
    这里只守住「表里写了但根本没人抛」这一种：那种行会让评审把不存在的失败路径当真。
    """

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "services" / "api" / "app").rglob("*.py")
    )
    missing = [
        row["code"]
        for row in draft["x-e2-errors"]["codes"]
        if row["code"] not in sources
    ]
    assert missing == [], f"草案登记了代码里不存在的错误码：{missing}"


def test_write_operations_require_if_match(draft):
    """四条写操作都必须声明 If-Match 与 428，且没有 404 之外的「假装成功」路径。"""

    for path, operations in draft["paths"].items():
        for method, declared in operations.items():
            if method in {"get", "head", "options"}:
                continue
            refs = [
                item.get("$ref", "")
                for item in declared.get("parameters") or []
                if isinstance(item, dict)
            ]
            assert any(ref.endswith("/IfMatch") for ref in refs), f"{method} {path} 缺 If-Match"
            assert "428" in declared["responses"], f"{method} {path} 缺 428"
