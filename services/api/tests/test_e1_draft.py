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

from app.main import create_app
from app.config import Settings
from app.routes import admin_policies, assessment_runs, enterprises, health, policies
from support import ADMIN_TOKEN, DEMO_SESSION_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
DRAFT_PATH = CONTRACTS / "openapi.e1-draft.yaml"
BASE_PATH = CONTRACTS / "openapi.yaml"

#: 草案 `x-e1-impact.current` 的三种写法 → 现行实现里的 router 级依赖。
#: 出现新写法时本文件会失败，提示把新形态补进来——避免「案里写了、代码里没有」静默通过。
CURRENT_FORMS = {
    "公开": [],
    "DemoSession（restricted 门禁，local 免）": ["require_demo_session"],
    "AdminBearer（local）/ DemoSession AND AdminBearer（restricted）": ["require_admin"],
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


def test_impact_rows_cover_exactly_the_mounted_operations():
    contract = _contract_operations(BASE_PATH)
    mounted = _mounted_operations()

    assert set(mounted) == set(contract), (
        "草案基线端点与 services/api 实际挂载的操作不一致："
        f"只在契约里 {sorted(set(contract) - set(mounted))}，"
        f"只在实现里 {sorted(set(mounted) - set(contract))}"
    )
    baseline = set(contract.values())
    assert len(baseline) == 16

    draft = yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))
    rows = {row["endpoint"] for row in draft["x-e1-impact"]}
    assert rows == baseline


def test_impact_current_column_matches_router_dependencies():
    """16 行的 `current` 是对**现行**实现的断言，逐条拿代码核对。"""

    contract = _contract_operations(BASE_PATH)
    mounted = _mounted_operations()
    draft = yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))

    mismatches = []
    for row in draft["x-e1-impact"]:
        endpoint = row["endpoint"]
        operation_id = next(oid for oid, ep in contract.items() if ep == endpoint)
        actual = sorted(mounted[operation_id])
        current = row["current"]
        assert current in CURRENT_FORMS, (
            f"{endpoint}: 未知的 current 写法 {current!r}，请在 CURRENT_FORMS 里补上对应依赖"
        )
        expected = sorted(CURRENT_FORMS[current])
        if actual != expected:
            mismatches.append(f"{endpoint}: 草案说 {current!r}，实现依赖是 {actual}")
    assert mismatches == [], "\n".join(mismatches)


def test_published_security_matches_current_classification():
    """服务端实际发布给客户端的 security：health 无、公开端点 DemoSession、后台两者 AND。"""

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
        "/admin/": [{"DemoSession": [], "AdminBearer": []}],
        "default": [{"DemoSession": []}],
    }
    problems = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "put", "post", "patch", "delete"}:
                continue
            if path.endswith("/health"):
                expected = expected_security["getHealth"]
            elif "admin" in (operation.get("tags") or []):
                # 与 services/api/app/main.py 的 _install_openapi 一致：按 tag 分类，
                # API-14 轨迹挂在 /assessment-runs 下但属 admin tag。
                expected = expected_security["/admin/"]
            else:
                expected = expected_security["default"]
            if operation.get("security") != expected:
                problems.append(f"{method.upper()} {path}: {operation.get('security')} != {expected}")
    assert problems == [], "\n".join(problems)
    assert len(schema["paths"]) == 16
