"""服务实际暴露的接口必须与 contracts/openapi.yaml 的已实现部分一致。"""

from __future__ import annotations

import pytest
import yaml

from app.config import APP_VERSION
from app.main import API_PREFIX, create_app
from support import CONTRACT_PATH

IMPLEMENTED = {
    ("get", "/health"): "getHealth",
    ("get", "/policies"): "listPolicies",
    ("get", "/policies/{policyId}"): "getPolicy",
    ("post", "/admin/policies/import"): "importPolicy",
    ("post", "/admin/policies/{policyId}/extract"): "extractPolicy",
    ("put", "/admin/policies/{policyId}/criteria"): "saveCriteria",
    ("post", "/admin/policies/{policyId}/publish"): "publishPolicy",
    ("get", "/admin/policies"): "listAdminPolicies",
    ("get", "/admin/policies/{policyId}"): "getAdminPolicy",
    ("post", "/enterprises"): "createEnterprise",
    ("get", "/enterprises/{enterpriseId}"): "getEnterprise",
    ("patch", "/enterprises/{enterpriseId}/facts/{factKey}"): "updateFact",
    ("post", "/assessment-runs"): "createRun",
    ("get", "/assessment-runs/{runId}"): "getRun",
    ("get", "/assessment-runs/{runId}/report.md"): "downloadReport",
    ("get", "/assessment-runs/{runId}/trace"): "getTrace",
    # E1（#46）的 7 个操作：自 1.1.0-rc.1 起已并入 contracts/openapi.yaml，
    # 不再是「只在草案里」的集合。
    ("post", "/auth/register"): "registerUser",
    ("post", "/auth/login"): "loginUser",
    ("post", "/auth/logout"): "logoutUser",
    ("get", "/auth/me"): "getCurrentUser",
    ("post", "/auth/password"): "changePassword",
    ("get", "/enterprises"): "listEnterprises",
    ("get", "/assessment-runs"): "listRuns",
}

#: E1（#46）新增的 7 个操作。它们同时写在正式契约与设计记录里，两边都要对得上。
E1_OPERATIONS = {
    ("post", "/auth/register"): "registerUser",
    ("post", "/auth/login"): "loginUser",
    ("post", "/auth/logout"): "logoutUser",
    ("get", "/auth/me"): "getCurrentUser",
    ("post", "/auth/password"): "changePassword",
    ("get", "/enterprises"): "listEnterprises",
    ("get", "/assessment-runs"): "listRuns",
}


@pytest.fixture(scope="module")
def contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def schema(app) -> dict:
    return app.openapi()


def test_every_implemented_endpoint_exists_in_contract(contract, schema):
    for method, path in IMPLEMENTED:
        assert path in contract["paths"], f"契约缺少 {path}"
        assert method in contract["paths"][path], f"契约缺少 {method.upper()} {path}"
        served = schema["paths"].get(f"{API_PREFIX}{path}")
        assert served is not None, f"服务未暴露 {path}"
        assert method in served, f"服务未暴露 {method.upper()} {path}"


def test_operation_ids_match(contract, schema):
    for (method, path), operation_id in IMPLEMENTED.items():
        expected = contract["paths"][path][method]["operationId"]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]["operationId"]
        assert actual == expected == operation_id


def test_summaries_and_tags_match(contract, schema):
    for method, path in IMPLEMENTED:
        expected = contract["paths"][path][method]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]
        assert actual["summary"] == expected["summary"]
        assert actual["tags"] == expected["tags"]


def test_security_matches(contract, schema):
    for method, path in IMPLEMENTED:
        expected = contract["paths"][path][method]["security"]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]["security"]
        assert actual == expected


def test_gate_matches_the_contract(contract, schema):
    """`x-e1-gate` 是 security 之外的第二个维度：门禁（restricted 的外层 demo cookie）。

    标准 ``security`` 只能表达「要哪些凭证」，表达不了「restricted 部署还要先过门禁」。
    PR #61 最新复审 P1 指的就是这个缺口：后台写在契约里只写了 ``UserSession``，读者
    会以为任何部署下拿着会话就能写；实际还要先过 demo 门禁。这里把两份文档的门禁
    逐操作锁死，只有 local/restricted 两个取值参与比较，note 是人读的说明。
    """

    for (method, path) in IMPLEMENTED:
        expected = contract["paths"][path][method]["x-e1-gate"]
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]["x-e1-gate"]
        assert actual["local"] == expected["local"] == "none", (method, path)
        assert actual["restricted"] == expected["restricted"], (method, path)
        assert actual["note"].strip(), f"{method.upper()} {path} 的门禁缺说明"

    model = contract["x-e1-gate-model"]
    served_model = schema["x-e1-gate-model"]
    assert served_model["exempt"] == model["exempt"]
    assert set(served_model["modes"]) == set(model["modes"]) == {"local", "restricted"}


def test_e1_operations_agree_across_contract_draft_and_server(contract, schema):
    """七个 E1 操作在正式契约、设计记录与运行中的服务三处必须一致。

    并入之后 ``contracts/openapi.e1-draft.yaml`` 只剩设计记录的职责（门禁/身份真值表、
    错误码、可复核样例）；端点与 schema 以 ``openapi.yaml`` 为准。这里锁住三处的
    operationId 与门禁，防止「改了正式契约、草案没回扫」这种两份文档各说一套。
    """

    draft = yaml.safe_load(
        (CONTRACT_PATH.parent / "openapi.e1-draft.yaml").read_text(encoding="utf-8")
    )
    for (method, path), operation_id in E1_OPERATIONS.items():
        declared = contract["paths"][path][method]
        recorded = draft["paths"][path][method]
        served = schema["paths"][f"{API_PREFIX}{path}"][method]

        assert declared["operationId"] == recorded["operationId"] == served["operationId"], path
        assert declared["operationId"] == operation_id, path
        for field in ("local", "restricted"):
            assert (
                declared["x-e1-gate"][field]
                == recorded["x-e1-gate"][field]
                == served["x-e1-gate"][field]
            ), (path, field)
        assert declared["security"] == recorded["security"] == served["security"], path


def test_the_gate_is_exempt_only_for_health_and_logout(schema):
    """豁免清单只有两项；其余操作在 restricted 下一律先过门禁。"""

    exempt_paths = {f"{API_PREFIX}/health", f"{API_PREFIX}/auth/logout"}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            gate = operation["x-e1-gate"]
            assert (gate["restricted"] == "exempt") is (path in exempt_paths), (method, path)


def test_security_schemes_are_served(schema):
    schemes = schema["components"]["securitySchemes"]
    assert schemes["AdminBearer"]["scheme"] == "bearer"
    assert schemes["DemoSession"]["name"] == "huice_demo_session"
    assert schemes["UserSession"]["name"] == "huice_session"


def test_published_security_keeps_the_bearer_read_only(contract):
    """E1 起运维 Bearer 只读：发布的契约不能把写操作或业务接口许诺给 Bearer。

    这条防的是「契约继续写着 DemoSession + AdminBearer、后端却 403」的回退——
    M2 在 PR #61 上指出的正是这个不一致。它直接读契约文件，与业务代码无关。
    """

    writes = [
        ("post", "/admin/policies/import"),
        ("post", "/admin/policies/{policyId}/extract"),
        ("put", "/admin/policies/{policyId}/criteria"),
        ("post", "/admin/policies/{policyId}/publish"),
    ]
    for method, path in writes:
        assert contract["paths"][path][method]["security"] == [{"UserSession": []}], path

    reads = [
        ("get", "/admin/policies"),
        ("get", "/admin/policies/{policyId}"),
        ("get", "/assessment-runs/{runId}/trace"),
    ]
    for method, path in reads:
        schemes = [next(iter(entry)) for entry in contract["paths"][path][method]["security"]]
        assert "AdminBearer" in schemes, path
        assert "DemoSession" not in schemes or path.endswith("/trace"), path

    for method, path in IMPLEMENTED:
        # 后台、自检与 AUTH-01—05 不适用「业务接口 = 会话或演示会话」这条口径：
        # 注册/登录/登出没有身份要求，/auth/me 与 /auth/password 只要会话。
        if path.startswith("/admin/") or path == "/health" or path.startswith("/auth/"):
            continue
        schemes = [next(iter(entry)) for entry in contract["paths"][path][method]["security"]]
        assert "DemoSession" in schemes and "UserSession" in schemes, path


def test_unimplemented_endpoints_are_not_served(schema):
    served = {
        (method, path.replace(API_PREFIX, "", 1))
        for path, operations in schema["paths"].items()
        for method in operations
    }
    assert served == set(IMPLEMENTED)


def test_e1_operations_are_declared_in_the_contract_and_the_draft(contract, schema):
    """七个 E1 操作两边都要写明：正式契约是行为来源，草案是设计记录。"""

    draft_path = CONTRACT_PATH.parent / "openapi.e1-draft.yaml"
    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    for (method, path), operation_id in E1_OPERATIONS.items():
        for label, doc in (("正式契约", contract), ("设计记录", draft)):
            declared = doc["paths"].get(path, {}).get(method)
            assert declared is not None, f"{label}缺少 {method.upper()} {path}"
            assert declared["operationId"] == operation_id
        actual = schema["paths"][f"{API_PREFIX}{path}"][method]
        assert actual["operationId"] == operation_id


def test_served_version_matches_the_contract(contract):
    """健康检查自报的版本必须等于契约的 ``info.version``。

    这条原先只断言契约是某个 rc 字面量，``config.APP_VERSION`` 却没人管——它在 rc.3
    之后就没再跟，契约走到 rc.5 时 ``/health`` 仍对外报 ``1.0.0-rc.3``（2026-11-01
    评审指出）。改成两边比对后，以后只改一侧会直接转红。
    """
    assert APP_VERSION == contract["info"]["version"]


# --------------------------------------------------- 事实字典的三处同步（rc.4 起）


def test_fact_key_enum_matches_the_server_dictionary(contract):
    """契约枚举、``schemas.FactKey`` 字面量、``rules.FACT_UNITS`` 是同一份字典的三份拷贝。

    三份里有任何一份漏键，都会表现成「契约看着支持、接口 422」或「接口能存、表达式
    校验拒绝」这种两头不一致，而不是一个显眼的报错。这里锁死三者相等。
    """

    from typing import get_args

    from app.domain.rules import FACT_UNITS
    from app.schemas import FactKey

    declared = contract["components"]["schemas"]["FactKey"]["enum"]
    assert set(get_args(FactKey)) == set(declared) == set(FACT_UNITS)


def test_fact_unit_enum_matches_the_server_dictionary(contract):
    from typing import get_args

    from app.domain.rules import FACT_UNITS
    from app.schemas import FactUnit

    declared = contract["components"]["schemas"]["FactUnit"]["enum"]
    assert set(get_args(FactUnit)) == set(declared)
    assert set(FACT_UNITS.values()) <= set(declared)


def test_every_contract_expression_operator_is_accepted_by_the_validator(contract):
    """契约 oneOf 里的每个算子都要能被 ``validate_expression`` 认可，反之亦然。

    rc.4 的教训：``DateExpression.factKey`` 的 schema 仍写死 ``establishedDate``，而
    说明和服务端都已经放宽到两个日期键——契约说一套、代码做一套，两边都没报错，直到
    有人照着 schema 写 AST 才会炸。这里把两边的算子集合与日期键集合一起锁死。
    """

    from app.domain.rules import DATE_KEYS, validate_expression

    schemas = contract["components"]["schemas"]
    declared: dict[str, dict] = {}
    for ref in schemas["Expression"]["oneOf"]:
        name = ref["$ref"].rsplit("/", 1)[-1]
        body = schemas[name]
        declared[body["properties"]["op"]["const"]] = body

    assert set(declared) == {
        "and",
        "or",
        "not",
        "compare",
        "date",
        "industry_exclusion",
        "quarterly_average",
        "manual_review",
    }
    # 每个算子都能过结构校验（样板：结构与常量一致）。
    samples = {
        "and": {"op": "and", "children": [{"op": "manual_review", "reason": "x"}, {"op": "manual_review", "reason": "y"}]},
        "or": {"op": "or", "children": [{"op": "manual_review", "reason": "x"}, {"op": "manual_review", "reason": "y"}]},
        "not": {"op": "not", "child": {"op": "manual_review", "reason": "x"}},
        "compare": {
            "op": "compare",
            "factKey": "taxableIncome",
            "period": "assessment",
            "comparator": "lte",
            "value": "3000000.00",
            "unit": "CNY",
        },
        "date": {
            "op": "date",
            "factKey": "establishedDate",
            "period": "assessment",
            "comparator": "lte",
            "value": "2026-06-30",
        },
        "industry_exclusion": {
            "op": "industry_exclusion",
            "factKey": "industry",
            "period": "assessment",
            "excluded": ["F51"],
        },
        "quarterly_average": {
            "op": "quarterly_average",
            "metric": "employeeCount",
            "period": {"startDate": "2025-01-01", "endDate": "2025-12-31"},
            "comparator": "lte",
            "value": "300",
            "unit": "person",
        },
        "manual_review": {"op": "manual_review", "reason": "须人工核查"},
    }
    assert set(samples) == set(declared)
    for op, node in samples.items():
        assert validate_expression(node) == [], op

    # date 算子的键集合两边必须一致（rc.4 就是这里没对上）。
    date_keys = declared["date"]["properties"]["factKey"]["enum"]
    assert set(date_keys) == set(DATE_KEYS)


# --------------------------------------------- 用量未知的表达（rc.7 起）


def test_model_usage_can_say_the_tokens_are_unknown(contract, schema):
    """rc.7：`ModelUsage` 多了可选 `tokensUnknown`，用来区分「上游没给用量」与真实 0。

    没有它时，2xx 但缺 `usage` 只能返回 0，消费者分不清「这次真的没花 token」和
    「不知道花了多少」——日志里的 unknown 传不到接口调用方。这里锁住三件事：
    契约里它是**可选**布尔、服务端字段名一致、缺省时按「已知」解释，并且**已知时
    序列化省略该字段**：`ModelUsage` 是 `additionalProperties: false`，若常路径也回
    `tokensUnknown: false`，按 rc.6 严格校验的旧消费者会连正常响应一起判非法。
    """

    contract_usage = contract["components"]["schemas"]["ModelUsage"]
    served_usage = schema["components"]["schemas"]["ModelUsage"]

    assert "tokensUnknown" in contract_usage["properties"]
    assert contract_usage["properties"]["tokensUnknown"]["type"] == "boolean"
    # 可选：放进 required 就等于对所有既有调用方强制新字段——那不是「常路径省略、
    # 只在未知时出现」的迁移，旧消费者会连已知用量的正常响应一起拒掉。
    assert "tokensUnknown" not in contract_usage["required"]
    assert "tokensUnknown" in served_usage["properties"]
    assert "tokensUnknown" not in served_usage["required"]

    from app.schemas import ModelUsage

    # 契约要求还是那五个字段；省略 tokensUnknown 时默认「已知」。
    assert set(contract_usage["required"]) == {
        "mode",
        "modelVersion",
        "inputTokens",
        "outputTokens",
        "elapsedMs",
    }
    usage = ModelUsage(
        mode="deepseek",
        model_version="deepseek-chat",
        input_tokens=1,
        output_tokens=2,
        elapsed_ms=3,
    )
    assert usage.tokens_unknown is False
    # 已知时不出现在线上响应里；未知时才带 `true`。
    assert "tokensUnknown" not in usage.model_dump(mode="json", by_alias=True)
    unknown = ModelUsage(
        mode="deepseek",
        model_version="deepseek-chat",
        input_tokens=0,
        output_tokens=0,
        elapsed_ms=3,
        tokens_unknown=True,
    )
    assert unknown.model_dump(mode="json", by_alias=True)["tokensUnknown"] is True


# ------------------------------------------------- 轨迹引用与引文的分工（rc.6 起）


def test_trace_step_refs_match_the_contract(contract, schema):
    """轨迹步骤带的是内部对象引用，不是引文（issue #27）。

    ``TraceStep.evidenceRefs`` 曾经和 ``CriterionResult.evidenceRefs`` 共用
    ``EvidenceRef``：服务端写的是 ``{type,id}``，契约要求引文字段，按契约校验的前端
    会把每条步骤判成契约错误、整页不展示。这里锁死字段名、引用类型与枚举三处。
    """

    from typing import get_args

    from app.schemas import TraceObjectRef

    contract_trace = contract["components"]["schemas"]["TraceStep"]
    served_trace = schema["components"]["schemas"]["TraceStep"]
    assert "objectRefs" in contract_trace["required"]
    assert "evidenceRefs" not in contract_trace["properties"]
    assert "objectRefs" in served_trace["required"]
    assert "evidenceRefs" not in served_trace["properties"]

    assert contract_trace["properties"]["objectRefs"]["items"]["$ref"].endswith(
        "/TraceObjectRef"
    )
    contract_ref = contract["components"]["schemas"]["TraceObjectRef"]
    assert contract_ref["additionalProperties"] is False
    assert set(contract_ref["required"]) == {"type", "id"}
    assert set(contract_ref["properties"]["type"]["enum"]) == set(
        get_args(TraceObjectRef.model_fields["type"].annotation)
    )

    # 条件结果那侧的 evidenceRefs 必须仍然是引文：两类信息不能又混回同一个 schema。
    criterion_items = contract["components"]["schemas"]["CriterionResult"]["properties"][
        "evidenceRefs"
    ]["items"]
    assert criterion_items["$ref"].endswith("/EvidenceRef")


def test_trace_step_rejects_untyped_references():
    """引用结构收紧到 ``TraceObjectRef``：多给字段或未知 ``type`` 直接 422，不再透传。"""

    from uuid import uuid4

    from pydantic import ValidationError

    from app.schemas import TraceStep

    base = {
        "sequence": 1,
        "stage": "retrieval",
        "status": "ok",
        "inputSummary": "检索",
        "outputSummary": "命中 1 项",
        "policyId": None,
        "policyVersion": None,
        "modelOrRuleVersion": "rules-demo-v1",
        "elapsedMs": 1,
        "errorCode": None,
        "humanAction": None,
    }

    TraceStep(objectRefs=[{"type": "policy", "id": str(uuid4())}], **base)

    for ref in (
        {"type": "quote", "id": str(uuid4())},
        {"type": "policy"},
        {"id": str(uuid4())},
        {"type": "policy", "id": str(uuid4()), "quote": "原文"},
    ):
        with pytest.raises(ValidationError):
            TraceStep(objectRefs=[ref], **base)


def test_register_username_constraints_match_the_server_rule(contract, schema):
    """用户名的契约约束必须与实现同一条规则（PR #61 复审第 1 条）。

    M2 指出过：契约写 3—64 位、服务端规则是 3—32 位，按契约构造的 33—64 位用户名能过
    schema 校验却被 422 拒绝。这里两件事一起锁住：

    1. **结构**：三份文档（正式契约、运行时 `/openapi.json`、草案）的 `RegisterRequest`
       与 `User` 的 `username` 都是同一组 `minLength`/`maxLength`/`pattern`，且与
       `app/auth.py` 的常量一致；
    2. **行为**：凡是契约会放行的字符串，服务端规则也必须放行（契约不得接受服务端随后
       拒掉的值）。反方向——契约比服务端更严——是允许且有意的：服务端先去首尾空白、转
       小写再套规则，所以契约字段说明写着「先规范化」。
    """

    from jsonschema import Draft202012Validator

    from app.auth import (
        MAX_USERNAME_LENGTH,
        MIN_USERNAME_LENGTH,
        USERNAME_PATTERN,
        normalize_username,
        username_is_valid,
    )

    documents = {
        "openapi.yaml": contract["components"]["schemas"],
        "运行时 /openapi.json": schema["components"]["schemas"],
    }
    for label, schemas in documents.items():
        for name in ("RegisterRequest", "User"):
            username = schemas[name]["properties"]["username"]
            assert username["minLength"] == MIN_USERNAME_LENGTH, (label, name)
            assert username["maxLength"] == MAX_USERNAME_LENGTH, (label, name)
            assert username["pattern"] == USERNAME_PATTERN.pattern, (label, name)

    validator = Draft202012Validator(
        contract["components"]["schemas"]["RegisterRequest"]["properties"]["username"]
    )
    samples = [
        "",
        " ",
        "ab",
        "abc",
        "a" * 32,
        "a" * 33,
        "A" * 20,
        "a" * 64,
        "acme.two",
        "a_b-c.d",
        "ab c",
        "ab/c",
        "用户abc",
        "ab@c",
    ]
    accepted_by_contract = [value for value in samples if validator.is_valid(value)]
    assert "a" * 33 not in accepted_by_contract, "契约不得放行 33 位用户名"
    assert "a" * 64 not in accepted_by_contract, "契约不得放行 64 位用户名"
    for value in accepted_by_contract:
        assert username_is_valid(normalize_username(value)), (
            f"契约放行了 {value!r}，服务端规则却拒绝它"
        )
