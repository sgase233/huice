"""材料要求的真实数据库用例：导入、按政策解析、以及在判断运行里展开成清单。

只在设置 ``HUICE_TEST_DATABASE_URL`` 时运行。``test_materials.py`` 覆盖纯函数口径，
这里覆盖「库里的那份材料要求确实落到了这次判断上」——包括政策代号怎么落到 UUID、
季度键怎么变成八个引用、关闭的政策怎么显示成关闭。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentContext
from app.agents.orchestrator import assess
from app.agents.profile import normalize_profile
from app.agents.report import render_markdown
from app.domain.rules import FACT_UNITS, quarters_of
from app.domain.versioning import SHANGHAI
from app.integrations.storage import CriterionRecord
from app.materials.catalog import MaterialError, parse
from app.schemas import AssessmentInput, EnterpriseInput
from db_support import create, query_all

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
AS_OF = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI)
DOCUMENT = "财政部 税务总局公告2023年第12号"
CODE = "CN-TAX-SME-2023-012"

INCOME_GATE = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "lte",
    "value": "3000000.00",
    "unit": "CNY",
}
HEADCOUNT_AVERAGE = {
    "op": "quarterly_average",
    "metric": "employeeCount",
    "period": "assessment",
    "comparator": "lte",
    "value": "300",
    "unit": "person",
}


def material(**overrides) -> dict[str, str]:
    base = {
        "policy_type": "tax",
        "material_id": "TAX-02",
        "title": "企业所得税年度纳税申报表",
        "required": "条件交",
        "condition_desc": "政策含应纳税所得额阈值判断",
        "description": "用于判断应纳税所得额是否满足阈值",
        "proves_fact_keys": "taxableIncome",
        "applies_to_policy_ids": CODE,
        "source_basis": "公告第五条",
        "review_status": "已仲裁",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def codes() -> dict[str, tuple[str, ...]]:
    return {CODE: (DOCUMENT,)}


def publish(repo, *, expressions, document_number=DOCUMENT, **overrides):
    overrides.setdefault("type", "tax")  # db_support 的默认是 grant；材料表默认按 tax 写
    draft = create(repo, version="v1.0", documentNumber=document_number, **overrides)
    body = draft.sources[0]
    spans = [
        {
            "sourceId": str(body.id),
            "sourceUrl": body.source_url,
            "sourceVersion": draft.version,
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
        version="v1.0",
        expected_revision=draft.revision,
        criteria=criteria,
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
        extraction_model=None,
        extraction_elapsed_ms=0,
        extraction_input_chars=0,
    )
    published = repo.publish(
        policy_id=draft.id,
        version="v1.0",
        expected_revision=saved.revision,
        trace_id=str(uuid4()),
    )
    return published.id, published


def enterprise(repo, *, income: str | None = None):
    facts = [
        {"key": "region", "value": "440300", "unit": "code", "period": YEAR, "source": "user_input"},
        {"key": "industry", "value": "C39", "unit": "code", "period": YEAR, "source": "user_input"},
        {"key": "taxableIncome", "value": income, "unit": "CNY", "period": YEAR, "source": "user_input"},
    ]
    profile = normalize_profile(
        EnterpriseInput.model_validate({"nameSynthetic": "合成企业A", "period": YEAR, "facts": facts})
    )
    return repo.create_enterprise(profile=profile, trace_id=str(uuid4()))


def assess_with(repo, enterprise_id, policy_id):
    request = AssessmentInput.model_validate(
        {
            "enterpriseId": str(enterprise_id),
            "policyIds": [str(policy_id)],
            "asOf": AS_OF.isoformat(),
            "period": YEAR,
        }
    )
    context = AgentContext(trace_id=str(uuid4()), as_of=request.as_of, period=PERIOD)
    return assess(request=request, context=context, repository=repo, llm=None)


def import_rows(repo, rows, *, codes):
    catalog = parse(rows, fact_keys=tuple(FACT_UNITS))
    return repo.import_material_requirements(catalog.importable, document_numbers=codes)


# ------------------------------------------------------------------- 导入


def test_import_resolves_the_business_code_to_the_policy(repo, codes, database_url_schema) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])

    summary = import_rows(repo, [material()], codes=codes)

    assert summary["imported"] == ["TAX-02"]
    assert summary["policies"] == {CODE: str(policy_id)}
    rows = query_all(
        database_url_schema,
        "SELECT material_id, policy_id FROM material_requirement_policy",
    )
    assert rows[0][0] == "TAX-02"
    assert str(rows[0][1]) == str(policy_id)


def test_a_code_with_two_published_documents_is_ambiguous(repo, database_url_schema) -> None:
    # 同一目录的新旧两版都已发布：代号落到两项政策，材料挂哪一项说不清
    publish(repo, expressions=[INCOME_GATE], document_number=DOCUMENT)
    publish(repo, expressions=[INCOME_GATE], document_number="国科发火〔2016〕32号")

    with pytest.raises(MaterialError, match=CODE):
        import_rows(repo, [material()], codes={CODE: (DOCUMENT, "国科发火〔2016〕32号")})


def test_an_unknown_policy_code_stops_the_whole_import(repo, codes, database_url_schema) -> None:
    publish(repo, expressions=[INCOME_GATE])
    other = material(material_id="TAX-03", applies_to_policy_ids="CN-TAX-RD-2023-007")

    with pytest.raises(MaterialError, match="CN-TAX-RD-2023-007"):
        import_rows(repo, [material(), other], codes=codes)

    # 整体失败，不留半份清单
    assert query_all(database_url_schema, "SELECT material_id FROM material_requirement") == []


def test_a_policy_that_is_not_published_yet_cannot_anchor_materials(repo, codes) -> None:
    create(repo, version="v1.0", documentNumber=DOCUMENT)  # 只导入，未发布

    with pytest.raises(MaterialError, match="没有已发布的政策版本"):
        import_rows(repo, [material()], codes=codes)


def test_reimport_replaces_the_previous_set(repo, codes, database_url_schema) -> None:
    publish(repo, expressions=[INCOME_GATE])

    import_rows(
        repo,
        [material(), material(material_id="TAX-03", applies_to_policy_ids=CODE)],
        codes=codes,
    )
    summary = import_rows(repo, [material()], codes=codes)

    assert summary["removed"] == ["TAX-03"]
    assert query_all(
        database_url_schema, "SELECT material_id FROM material_requirement ORDER BY 1"
    ) == [("TAX-02",)]


def test_a_tax_material_cannot_hang_on_a_grant_policy(repo, codes) -> None:
    publish(repo, expressions=[INCOME_GATE], type="grant")

    with pytest.raises(MaterialError, match="类型必须一致"):
        import_rows(repo, [material(policy_type="tax")], codes=codes)


# --------------------------------------------------------------- 判断运行


def test_the_run_lists_only_materials_the_policy_actually_uses(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])
    import_rows(
        repo,
        [
            material(),  # taxableIncome：政策用到了
            material(
                material_id="TAX-06",
                title="各季度从业人数统计",
                condition_desc="政策含从业人数门槛",
                proves_fact_keys="employeeCountStart;employeeCountEnd",
            ),  # 从业人数：这条政策没用到
        ],
        codes=codes,
    )

    run = assess_with(repo, enterprise(repo).id, policy_id)

    items = run.assessments[0].preparation.items
    assert [item.material_id for item in items] == ["TAX-02"]
    assert items[0].status == "facts_missing"
    assert items[0].missing is True


def test_facts_in_hand_mark_the_item_facts_filled(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])
    import_rows(repo, [material()], codes=codes)

    run = assess_with(repo, enterprise(repo, income="2800000.00").id, policy_id)

    items = run.assessments[0].preparation.items
    assert items[0].status == "facts_filled"
    assert items[0].missing is False

    # 报告里也不能把这句写成「材料已交」：事实有取值 ≠ 材料交上来了，
    # 服务端自始至终没看过材料本身（M2 在 #40 的 P1）。
    markdown = render_markdown(run, downloaded_at=AS_OF)
    assert "所需事实都有取值，但不代表材料已交" in markdown
    assert "已提交" not in markdown
    assert "已提供" not in markdown


def test_a_material_without_fact_keys_is_flagged_for_manual_check(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])
    import_rows(
        repo,
        [
            material(
                material_id="TAX-01",
                title="营业执照（登记地址、经营范围、成立日期）",
                required="必交",
                condition_desc="",
                description="登记类材料",
                proves_fact_keys="region;industry",
                applies_to_policy_ids="",
            )
        ],
        codes=codes,
    )

    run = assess_with(repo, enterprise(repo).id, policy_id)

    item = run.assessments[0].preparation.items[0]
    assert item.material_id == "TAX-01"
    # 登记类材料的两条事实都有取值 → 状态是「事实齐了」，但这不等于材料已交
    assert item.status == "facts_filled"
    assert item.title.startswith("【必交】")


def test_materials_without_fact_keys_need_a_human(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])
    import_rows(
        repo,
        [
            # 真实 CSV 里 GRANT-02 就是「条件交 + 空键」：条件只有文字，程序判断不了
            material(
                material_id="GRANT-02",
                title="项目申请书",
                required="条件交",
                condition_desc="资金项目在线申报入口",
                description="申报要件，不驱动事实核验",
                proves_fact_keys="",
                applies_to_policy_ids="",
            )
        ],
        codes=codes,
    )

    run = assess_with(repo, enterprise(repo).id, policy_id)

    item = run.assessments[0].preparation.items[0]
    assert item.status == "manual_check"
    assert item.missing is True
    assert item.required_fact_refs == []


def test_quarterly_material_expands_into_eight_refs(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[HEADCOUNT_AVERAGE])
    import_rows(
        repo,
        [
            material(
                material_id="TAX-06",
                title="各季度从业人数统计",
                proves_fact_keys="employeeCountStart;employeeCountEnd",
            )
        ],
        codes=codes,
    )

    run = assess_with(repo, enterprise(repo).id, policy_id)

    item = run.assessments[0].preparation.items[0]
    assert len(item.required_fact_refs) == 8
    expected = {
        (key, quarter[0], quarter[1])
        for quarter in quarters_of(2026)
        for key in ("employeeCountStart", "employeeCountEnd")
    }
    assert {
        (ref.key, ref.period.start_date, ref.period.end_date) for ref in item.required_fact_refs
    } == expected
    assert item.status == "facts_missing"


def test_a_closed_policy_does_not_ask_for_materials(repo, codes) -> None:
    policy_id, _ = publish(
        repo,
        expressions=[INCOME_GATE],
        publishedAt="2025-09-01",
        effectiveFrom="2025-10-01",
        effectiveTo="2025-12-31",
    )
    import_rows(repo, [material()], codes=codes)

    run = assess_with(repo, enterprise(repo).id, policy_id)

    item = run.assessments[0].preparation.items[0]
    assert item.status == "policy_closed"
    assert item.missing is False


def test_the_report_says_which_kind_of_gap_it_is(repo, codes) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])
    import_rows(
        repo,
        [
            material(),
            material(
                material_id="TAX-01",
                title="营业执照",
                required="必交",
                condition_desc="",
                proves_fact_keys="",
                applies_to_policy_ids="",
            ),
        ],
        codes=codes,
    )

    run = assess_with(repo, enterprise(repo).id, policy_id)
    markdown = render_markdown(run, downloaded_at=AS_OF)

    assert "【条件交】企业所得税年度纳税申报表" in markdown
    assert "（所需事实有缺项）" in markdown
    assert "（需人工核对）" in markdown
    # 报告也不许说「缺材料」：它只知道事实缺项，不知道材料交没交
    assert "缺材料" not in markdown
    # 有清单时不该再说「待政策材料导入后补齐」
    assert "待政策材料导入后补齐" not in markdown


def test_a_run_without_materials_keeps_the_old_warning(repo) -> None:
    policy_id, _ = publish(repo, expressions=[INCOME_GATE])

    run = assess_with(repo, enterprise(repo).id, policy_id)

    assert run.assessments[0].preparation.items == []
    assert any("待 M3 审核材料导入后补齐" in warning for warning in run.assessments[0].warnings)
