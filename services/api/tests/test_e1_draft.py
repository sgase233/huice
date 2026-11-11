"""E1 草案的独立校验：结构（contracts/check_e1_draft.py）+ 与现行实现的对照。

这里把两份文件放在一起对：``contracts/openapi.e1-draft.yaml`` 说「现行 16 个端点是什么样」，
``services/api`` 的 FastAPI 路由是事实。M2 在 #55 要求「逐端点核对门禁、身份、角色、对象归属、
CSRF 与失败响应」；能机器判的部分（端点清单、外层门禁、发布出去的 security）在本文件里判，
不能机器判的部分（角色/归属/CSRF 的**未来**口径）由草案正文与 PR 里的评审表负责。

本文件不做实现验收：E1 尚未实现，「身份/角色/归属/CSRF」这些列描述的是 E1 目标行为。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from app.main import API_PREFIX, create_app
from app.config import Settings
from app.routes import admin_policies, assessment_runs, enterprises, health, policies
from support import ADMIN_TOKEN, DEMO_SESSION_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
DRAFT_PATH = CONTRACTS / "openapi.e1-draft.yaml"
BASE_PATH = CONTRACTS / "openapi.yaml"

#: 草案 `x-e1-impact.gate.restricted` 的取值 → 现行实现里路由器级的门禁依赖。
#: （`current` 一列描述的是 E1 之前的实现，已随 E1 落地变成历史说明。）
GATE_FORMS = {
    "DemoSession": ["require_demo_session"],
    "none": [],
    "exempt": [],
}


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_e1_draft", CONTRACTS / "check_e1_draft.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _mounted_operations() -> dict[str, list[str]]:
    """operationId → 该操作的 router 级依赖名（门禁就是这一层）。

    直接查模块级 APIRouter，而不是遍历 app.routes：FastAPI 0.161 起 include_router 会
    保留成 ``_IncludedRouter`` 惰性节点，逐层展开属于实现细节，这里只依赖稳定的
    ``APIRouter.routes`` 与 ``APIRoute.dependant.dependencies``。
    """

    operations: dict[str, list[str]] = {}
    for module in (health, policies, enterprises, assessment_runs, admin_policies):
        for attr in ("router", "admin_router"):
            router = getattr(module, attr, None)
            if router is None:
                continue
            for route in router.routes:
                callables = [
                    getattr(dep.call, "__name__", repr(dep.call))
                    for dep in route.dependant.dependencies
                ]
                operations[route.operation_id] = callables
    return operations


def _contract_operations(path: Path) -> dict[str, str]:
    """操作契约里的 operationId → "METHOD /path"。"""

    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for route, item in doc["paths"].items():
        for method, operation in item.items():
            if method in {"get", "put", "post", "patch", "delete", "head", "options"}:
                out[operation["operationId"]] = f"{method.upper()} {route}"
    return out


def test_draft_validator_passes():
    counts, errors = _load_checker().check()
    assert errors == [], "草案校验未通过：\n" + "\n".join(errors)
    assert counts["impact_rows"] == 16
    assert counts["base_endpoints"] == 16
    assert counts["draft_operations"] == 7


#: E1 新加、只写在草案里的操作；它们不在 ``x-e1-impact``（那一表只写现有 16 个端点
#: 的改造）。并入 ``openapi.yaml`` 之后这些行应当删除或改写成「已落地」。
E1_ONLY_OPERATIONS = {"listEnterprises", "listRuns"}


def test_impact_rows_cover_exactly_the_mounted_operations():
    contract = _contract_operations(BASE_PATH)
    mounted = _mounted_operations()
    baseline_mounted = {
        operation_id: deps
        for operation_id, deps in mounted.items()
        if operation_id not in E1_ONLY_OPERATIONS
    }

    assert set(baseline_mounted) == set(contract), (
        "草案基线端点与 services/api 实际挂载的操作不一致："
        f"只在契约里 {sorted(set(contract) - set(baseline_mounted))}，"
        f"只在实现里 {sorted(set(baseline_mounted) - set(contract))}"
    )
    # E1 的两个列表接口必须真的挂上（草案写了、代码没有，是最容易漏的一项）。
    assert E1_ONLY_OPERATIONS <= set(mounted)
    baseline = set(contract.values())
    assert len(baseline) == 16

    draft = yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))
    rows = {row["endpoint"] for row in draft["x-e1-impact"]}
    assert rows == baseline


def test_impact_gate_column_matches_router_dependencies():
    """16 行的 `gate.restricted` 是 E1 后的门禁断言，逐条拿代码核对。

    E1 之前的写法是「门禁 + 身份」压在一个路由器级依赖里（Bearer 或 demo cookie），
    所以 ``x-e1-impact.current`` 描述的是改造前的代码，已无法拿现在的实现对；
    现在路由器上只剩门禁一层，身份与角色都在处理器里。
    """

    contract = _contract_operations(BASE_PATH)
    mounted = _mounted_operations()
    draft = yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))

    mismatches = []
    for row in draft["x-e1-impact"]:
        endpoint = row["endpoint"]
        operation_id = next(oid for oid, ep in contract.items() if ep == endpoint)
        actual = sorted(mounted[operation_id])
        restricted = row["gate"]["restricted"]
        assert restricted in GATE_FORMS, (
            f"{endpoint}: 草案出现未登记的门禁写法 {restricted!r}，"
            "请核对后补进本文件的 GATE_FORMS"
        )
        expected = sorted(GATE_FORMS[restricted])
        if actual != expected:
            mismatches.append(
                f"{endpoint}: 草案门禁是 {restricted!r}，实现依赖是 {actual}"
            )
    assert mismatches == [], "\n".join(mismatches)


def test_published_security_matches_current_classification():
    """服务端实际发布给客户端的 security。

    草案的口径是「``security`` 只表达身份，门禁写 ``x-e1-gate``」。E1 起这 16 个端点
    的 security 已按**真实授权**改写（#61 review 的 P1）：业务接口是会话或演示会话，
    后台读是会话或运维 Bearer，后台写只有会话——Bearer 只读。新增的 AUTH-01—05 按
    草案逐条给：注册/登录/登出不要求身份（``[]``），``/auth/me`` 与
    ``/auth/password`` 只要会话。
    """

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
    schema = application.openapi()

    expected_security = {
        "getHealth": [],
        "authPublic": [],
        "authSession": [{"UserSession": []}],
        # 后台读：会话或只读 Bearer；后台写：只有会话（E1 起 Bearer 只读）。
        "adminRead": [{"UserSession": []}, {"AdminBearer": []}],
        "adminWrite": [{"UserSession": []}],
        # 轨迹：本人、审核员会话与运维 Bearer 都能读，演示会话也能看无主样本的轨迹。
        "trace": [{"UserSession": []}, {"AdminBearer": []}, {"DemoSession": []}],
        "default": [{"UserSession": []}, {"DemoSession": []}],
    }
    #: 草案写明 security 为空的三条；其余两条只认会话。
    public_auth = {"/auth/register", "/auth/login", "/auth/logout"}
    session_auth = {"/auth/me", "/auth/password"}

    problems = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "put", "post", "patch", "delete"}:
                continue
            bare = path.removeprefix(API_PREFIX)
            if path.endswith("/health"):
                expected = expected_security["getHealth"]
            elif bare in public_auth:
                expected = expected_security["authPublic"]
            elif bare in session_auth:
                expected = expected_security["authSession"]
            elif bare.endswith("/trace"):
                expected = expected_security["trace"]
            elif "admin" in (operation.get("tags") or []):
                # 与 services/api/app/main.py 的 _security_for 一致：API-14 轨迹挂在
                # /assessment-runs 下但属 admin tag，故上面先判 trace 再判 tag。
                expected = (
                    expected_security["adminRead"]
                    if method == "get"
                    else expected_security["adminWrite"]
                )
            else:
                expected = expected_security["default"]
            if operation.get("security") != expected:
                problems.append(f"{method.upper()} {path}: {operation.get('security')} != {expected}")
    assert problems == [], "\n".join(problems)
    assert len(schema["paths"]) == 21
