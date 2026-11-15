"""E3 草案与实现的对照：3 个统计操作、组件名、``dateProvenance`` 形状。

E3 已随 ``openapi.yaml`` 的 ``1.3.0-rc.1`` 并入正式契约，``openapi.e3-draft.yaml``
保留为设计记录。这里锁三件事：草案与实现发布的操作一致；3 个路径与 ``dateProvenance``
确实进了正式契约；前端类型仍待 M2 重新生成（由 CI 的 ``generate_types --check`` 提示）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.analytics.completeness import DEFINITION_VERSION, APPLICABILITY_MODE
from app.config import Settings
from app.main import API_PREFIX, create_app
from support import ADMIN_TOKEN, DEMO_SESSION_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
DRAFT_PATH = REPO_ROOT / "contracts" / "openapi.e3-draft.yaml"
BASE_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

E3_OPERATIONS = {
    ("get", "/admin/metrics/review-workload"): "getReviewWorkload",
    ("get", "/admin/metrics/assessment-runs"): "getAssessmentRunTrends",
    ("get", "/admin/metrics/completeness"): "getProfileCompleteness",
}


def _draft() -> dict:
    return yaml.safe_load(DRAFT_PATH.read_text(encoding="utf-8"))


def _served() -> dict:
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


def test_the_draft_operations_match_the_served_operations() -> None:
    draft = _draft()
    served = _served()

    assert set(draft["paths"]) == {path for _method, path in E3_OPERATIONS}
    for (method, path), operation_id in E3_OPERATIONS.items():
        assert draft["paths"][path][method]["operationId"] == operation_id
        published = served["paths"][f"{API_PREFIX}{path}"][method]
        assert published["operationId"] == operation_id
        assert draft["paths"][path][method]["security"] == published["security"]
        expected_gate = draft["paths"][path][method]["x-e1-gate"]
        served_gate = published["x-e1-gate"]
        for mode in ("local", "restricted"):
            assert expected_gate[mode] == served_gate[mode]


def test_the_draft_defines_the_components_it_references() -> None:
    draft = _draft()
    schemas = draft["components"]["schemas"]

    assert {
        "MetricAvailability",
        "ReviewWorkloadResponse",
        "AssessmentRunsResponse",
        "CompletenessStatus",
        "CompletenessUnit",
        "CompletenessGroup",
        "CompletenessTotals",
        "CompletenessResponse",
        "DateProvenance",
        "DateProvenanceField",
    } <= set(schemas)
    assert schemas["CompletenessResponse"]["properties"]["definitionVersion"]["const"] == (
        DEFINITION_VERSION
    )
    assert schemas["CompletenessResponse"]["properties"]["applicabilityMode"]["const"] == (
        APPLICABILITY_MODE
    )


def test_the_operations_are_merged_into_the_main_contract() -> None:
    base = yaml.safe_load(BASE_PATH.read_text(encoding="utf-8"))

    for _method, path in E3_OPERATIONS:
        assert path in base["paths"], f"{path} 还没进正式契约"
    for name in ("PolicySummary", "PolicyDetail", "PolicyDraft"):
        assert "dateProvenance" in base["components"]["schemas"][name]["properties"]
    assert {
        "DateProvenance",
        "DateProvenanceField",
        "CompletenessResponse",
        "ReviewWorkloadResponse",
        "AssessmentRunsResponse",
    } <= set(base["components"]["schemas"])


def test_date_provenance_is_optional_in_the_three_policy_responses() -> None:
    """字段是可选且缺省省略：旧消费者按 ``additionalProperties: false`` 也读得动。"""

    from app.schemas import PolicyDetail, PolicyDraft, PolicySummary

    for model in (PolicySummary, PolicyDetail, PolicyDraft):
        field = model.model_fields["date_provenance"]
        assert field.default is None
