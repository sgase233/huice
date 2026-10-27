"""在线链路的真实数据库用例：企业画像、公开列表、判断运行的落库与读取视图。

与 ``test_agent_flow.py``（假仓储，验证 HTTP 契约）互补：这里验证真正的 SQL——
``factVersion`` 的 compare-and-swap、失效事件只打进读过该事实的 run、公开列表
只含已发布版本、``save_run`` 之后 ``get_run`` 能原样读回快照。

仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agents.contracts import AgentContext
from app.agents.orchestrator import assess
from app.agents.profile import normalize_fact_update, normalize_profile
from app.domain.versioning import SHANGHAI
from app.errors import ApiProblem
from app.integrations.storage import CriterionRecord
from app.schemas import AssessmentInput, EnterpriseInput, FactUpdate, PolicyImportMetadata
from db_support import create, query_all, source

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
AS_OF = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI)

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "gte",
    "value": "3000000.00",
    "unit": "CNY",
}


# ------------------------------------------------------------------ 构造工具


def enterprise_input(industry: str = "C39", income=None) -> EnterpriseInput:
    return EnterpriseInput.model_validate(
        {
            "nameSynthetic": "合成企业A",
            "period": YEAR,
            "facts": [
                {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "industry", "value": industry, "unit": "code", "period": YEAR, "source": "user_input"},
                {"key": "taxableIncome", "value": income, "unit": "CNY", "period": YEAR, "source": "user_input"},
            ],
        }
    )


def publish_policy(repo, *, expressions, version: str = "v1.0", policy_id=None, **overrides):
    """导入 → 保存已通过校验的条款 → 发布，返回 (policy_id, 已发布草稿)。"""

    draft = create(repo, version=version, policy_id=policy_id, **overrides)
    body = draft.sources[0]
    spans = [
        {
            "sourceId": str(body.id),
            "sourceUrl": body.source_url,
            "sourceVersion": version,
            "page": None,
            "section": "合成演示条款",
            "quote": "此处仅为接口结构演示，不是真实政策。",
            "textHash": body.content_hash,
        }
    ]
    criteria = [
        CriterionRecord(
            id=uuid4(),
            title=f"演示条件 {index + 1}",
            expression=expression,
            spans=spans,
            review_finding="none",
            validation_status="passed",
        )
        for index, expression in enumerate(expressions)
    ]
    saved = repo.save_criteria(
        policy_id=draft.id,
        version=version,
        expected_revision=draft.revision,
        criteria=criteria,
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
    )
    published = repo.publish(
        policy_id=draft.id,
        version=version,
        expected_revision=saved.revision,
        trace_id=str(uuid4()),
    )
    return published.id, published


def run_assessment(repo, enterprise_id, policy_ids, **overrides):
    body = {
        "enterpriseId": str(enterprise_id),
        "policyIds": [str(item) for item in policy_ids],
        "asOf": AS_OF.isoformat(),
        "period": YEAR,
    }
    body.update(overrides)
    request = AssessmentInput.model_validate(body)
    context = AgentContext(trace_id=str(uuid4()), as_of=request.as_of, period=PERIOD)
    return assess(request=request, context=context, repository=repo, llm=None)


def make_enterprise(repo, **kwargs):
    profile = normalize_profile(enterprise_input(**kwargs))
    return repo.create_enterprise(profile=profile, trace_id=str(uuid4()))


# -------------------------------------------------------------- 企业画像存储


def test_enterprise_roundtrip_keeps_unknown_as_null_and_version_stable(repo):
    created = make_enterprise(repo)

    loaded = repo.get_enterprise(created.id)

    assert loaded.id == created.id
    assert loaded.fact_version == created.fact_version
    assert [fact.key for fact in loaded.facts] == ["industry", "region", "taxableIncome"]
    income = next(fact for fact in loaded.facts if fact.key == "taxableIncome")
    assert income.value is None
    assert all(fact.verified is False for fact in loaded.facts)


def test_update_fact_is_compare_and_swap(repo):
    created = make_enterprise(repo)
    update = FactUpdate.model_validate(
        {"value": "2800000.00", "unit": "CNY", "period": YEAR, "source": "user_input"}
    )
    fact = normalize_fact_update(key="taxableIncome", update=update, profile_period=PERIOD)

    updated = repo.update_fact(
        enterprise_id=created.id,
        fact=fact,
        expected_fact_version=created.fact_version,
        trace_id=str(uuid4()),
    )
    assert updated.fact_version != created.fact_version

    with pytest.raises(ApiProblem) as conflict:
        repo.update_fact(
            enterprise_id=created.id,
            fact=fact,
            expected_fact_version=created.fact_version,
            trace_id=str(uuid4()),
        )
    assert conflict.value.status_code == 409
    assert conflict.value.details["currentVersion"] == updated.fact_version


def test_unknown_enterprise_is_404(repo):
    with pytest.raises(ApiProblem) as missing:
        repo.get_enterprise(uuid4())
    assert missing.value.status_code == 404


# ---------------------------------------------------------------- 公开列表


def test_public_list_only_contains_published_versions(repo):
    publish_policy(repo, expressions=[INDUSTRY_GATE])
    create(repo, version="v9.0")  # 仍是 pending_parse

    page = repo.list_public_policies(
        status=None, policy_type=None, region=None, as_of=None, cursor=None, limit=20
    )

    assert len(page.items) == 1
    assert page.items[0].status == "published"
    assert page.items[0].effective_from == date(2026, 10, 1)


def test_public_list_filters_by_region_and_as_of(repo):
    publish_policy(repo, expressions=[INDUSTRY_GATE], region="440300")
    publish_policy(repo, expressions=[INDUSTRY_GATE], region="440100")
    publish_policy(
        repo,
        expressions=[INDUSTRY_GATE],
        region="440100",
        publishedAt="2025-12-01",
        effectiveFrom="2026-01-01",
        effectiveTo="2026-06-30",
    )

    shenzhen = repo.list_public_policies(
        status=None, policy_type=None, region="440300", as_of=None, cursor=None, limit=20
    )
    assert len(shenzhen.items) == 1
    assert shenzhen.items[0].region == "440300"

    december = repo.list_public_policies(
        status=None,
        policy_type=None,
        region=None,
        as_of=date(2026, 12, 31),
        cursor=None,
        limit=20,
    )
    assert len(december.items) == 2  # 六月到期的那项不再适用


def test_public_list_cursor_is_bound_to_the_filters(repo):
    for _ in range(3):
        publish_policy(repo, expressions=[INDUSTRY_GATE])

    first = repo.list_public_policies(
        status=None, policy_type=None, region=None, as_of=None, cursor=None, limit=1
    )
    assert len(first.items) == 1
    assert first.next_cursor

    second = repo.list_public_policies(
        status=None, policy_type=None, region=None, as_of=None, cursor=first.next_cursor, limit=1
    )
    assert len(second.items) == 1
    assert second.items[0].id != first.items[0].id

    with pytest.raises(ApiProblem) as invalid:
        repo.list_public_policies(
            status=None,
            policy_type="tax",
            region=None,
            as_of=None,
            cursor=first.next_cursor,
            limit=1,
        )
    assert invalid.value.status_code == 400


def test_public_detail_needs_published_version_with_sources(repo):
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])

    detail = repo.get_policy_detail(policy_id)

    assert detail.status == "published"
    assert detail.criteria[0].validation_status == "passed"
    assert detail.sources[0].text

    with pytest.raises(ApiProblem) as missing:
        repo.get_policy_detail(policy_id, "v2.0")
    assert missing.value.status_code == 404


# -------------------------------------------------------------- 政策快照


def test_snapshots_reject_unpublished_policies(repo):
    draft = create(repo, version="v1.0")

    with pytest.raises(ApiProblem) as unpublished:
        repo.published_policy_snapshots([draft.id])
    assert unpublished.value.status_code == 422
    assert unpublished.value.code == "POLICY_NOT_PUBLISHED"


def test_snapshots_reject_criteria_that_failed_citation_checks(repo):
    draft = create(repo, version="v1.0")
    repo.save_criteria(
        policy_id=draft.id,
        version="v1.0",
        expected_revision=draft.revision,
        criteria=[
            CriterionRecord(
                id=uuid4(),
                title="未通过校验的条件",
                expression=INDUSTRY_GATE,
                spans=[],
                review_finding="none",
                validation_status="failed",
            )
        ],
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
    )

    with pytest.raises(ApiProblem) as blocked:
        repo.published_policy_snapshots([draft.id])
    # 未发布优先报告，这是最容易让调用方误解的原因。
    assert blocked.value.code == "POLICY_NOT_PUBLISHED"


# ---------------------------------------------------------- 判断运行落库


def test_run_is_saved_and_read_back_unchanged(repo):
    enterprise = make_enterprise(repo, industry="F51")
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])

    run = run_assessment(repo, enterprise.id, [policy_id])
    loaded = repo.get_run(run.id)

    assert loaded.id == run.id
    assert loaded.fact_version == run.fact_version
    assert loaded.assessments[0].result == run.assessments[0].result == "not_satisfied"
    assert loaded.assessments[0].validity == "current"
    assert loaded.assessments[0].stale_reasons == []
    assert loaded.policy_version_map == {str(policy_id): "v1.0"}


def test_fact_change_makes_only_dependent_assessments_stale(repo):
    enterprise = make_enterprise(repo)  # taxableIncome 未知
    policy_id, _ = publish_policy(repo, expressions=[INCOME_GATE])
    run = run_assessment(repo, enterprise.id, [policy_id])
    assert run.assessments[0].result == "insufficient_evidence"

    # 改一条与该政策无关的事实：不产生失效事件。
    update = FactUpdate.model_validate(
        {"value": "100", "unit": "person", "period": {"startDate": "2026-01-01", "endDate": "2026-03-31"}, "source": "user_input"}
    )
    repo.update_fact(
        enterprise_id=enterprise.id,
        fact=normalize_fact_update(
            key="employeeCountStart", update=update, profile_period=PERIOD
        ),
        expected_fact_version=enterprise.fact_version,
        trace_id=str(uuid4()),
    )
    assert repo.get_run(run.id).assessments[0].validity == "current"

    # 改用到的事实：旧 run 标 stale，四态与解释保持原样。
    current = repo.get_enterprise(enterprise.id)
    income = FactUpdate.model_validate(
        {"value": "2800000.00", "unit": "CNY", "period": YEAR, "source": "user_input"}
    )
    repo.update_fact(
        enterprise_id=enterprise.id,
        fact=normalize_fact_update(key="taxableIncome", update=income, profile_period=PERIOD),
        expected_fact_version=current.fact_version,
        trace_id=str(uuid4()),
    )

    stale = repo.get_run(run.id).assessments[0]
    assert stale.validity == "stale"
    assert stale.result == "insufficient_evidence"
    assert [reason.code for reason in stale.stale_reasons] == ["fact_changed"]
    assert stale.stale_reasons[0].changed_fact_refs[0].key == "taxableIncome"
    assert len(stale.stale_reasons[0].changed_criterion_ids) == 1


def test_publishing_a_new_version_makes_the_old_run_stale(repo):
    enterprise = make_enterprise(repo, industry="F51")
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])
    run = run_assessment(repo, enterprise.id, [policy_id])

    publish_policy(repo, expressions=[INDUSTRY_GATE], version="v2.0", policy_id=policy_id)

    stale = repo.get_run(run.id).assessments[0]
    assert stale.validity == "stale"
    assert [reason.code for reason in stale.stale_reasons] == ["policy_changed"]
    # 历史快照里的版本映射不变。
    assert repo.get_run(run.id).policy_version_map == {str(policy_id): "v1.0"}


def test_recompute_requires_the_same_enterprise_and_period(repo):
    enterprise = make_enterprise(repo, industry="F51")
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])
    first = run_assessment(repo, enterprise.id, [policy_id])

    second = run_assessment(repo, enterprise.id, [policy_id], supersedesRunId=str(first.id))
    assert second.id != first.id
    assert second.supersedes_run_id == first.id

    with pytest.raises(ApiProblem) as rejected:
        run_assessment(
            repo,
            enterprise.id,
            [policy_id],
            supersedesRunId=str(first.id),
            period={"startDate": "2025-01-01", "endDate": "2025-12-31"},
        )
    assert rejected.value.status_code == 422


def test_run_rows_are_append_only(repo, database_url_schema):
    enterprise = make_enterprise(repo, industry="F51")
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])
    run = run_assessment(repo, enterprise.id, [policy_id])

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url_schema) as conn:
            conn.execute("DELETE FROM assessment WHERE run_id = %s", (run.id,))
            conn.commit()

    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database_url_schema) as conn:
            conn.execute(
                "UPDATE assessment_run SET as_of = as_of - interval '1 day' WHERE id = %s",
                (run.id,),
            )
            conn.commit()

    rows = query_all(
        database_url_schema,
        "SELECT stage, status FROM trace_step WHERE run_id = %s ORDER BY sequence",
        (run.id,),
    )
    assert [row[0] for row in rows] == [
        "profile",
        "retrieval",
        "evaluation",
        "questioning",
        "explanation",
    ]
    assert all(row[1] == "ok" for row in rows)


def test_trace_links_ingestion_and_review_steps_before_online_steps(repo):
    enterprise = make_enterprise(repo, industry="F51")
    policy_id, _ = publish_policy(repo, expressions=[INDUSTRY_GATE])
    run = run_assessment(repo, enterprise.id, [policy_id])

    trace = repo.get_trace(run.id)

    stages = [step.stage for step in trace.steps]
    # 先列入库/审核轨迹，再列本次在线轨迹。
    assert stages[0] == "ingestion"
    assert "citation_validation" in stages
    assert "human_review" in stages
    assert stages[-5:] == ["profile", "retrieval", "evaluation", "questioning", "explanation"]
    # 两组各自的 sequence 从 1 开始，拼接后必须重排成严格递增。
    assert [step.sequence for step in trace.steps] == list(range(1, len(stages) + 1))

    ingestion = trace.steps[0]
    assert ingestion.policy_id == policy_id
    assert ingestion.policy_version == "v1.0"
    assert ingestion.input_summary and ingestion.output_summary
    # 发布步骤由人工确认，带 humanAction。
    review = next(step for step in trace.steps if step.stage == "human_review")
    assert review.human_action == "approve"

    online = trace.steps[-5:]
    assert all(step.policy_id is None for step in online)


def test_trace_of_unknown_run_is_404(repo):
    with pytest.raises(ApiProblem) as missing:
        repo.get_trace(uuid4())
    assert missing.value.status_code == 404


def test_unknown_run_is_404(repo):
    with pytest.raises(ApiProblem) as missing:
        repo.get_run(uuid4())
    assert missing.value.status_code == 404
    assert repo.get_run_header(uuid4()) is None
