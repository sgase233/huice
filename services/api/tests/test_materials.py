"""材料要求的解析与运行期展开（纯函数，不连库）。

三条口径来自 M3 的 `docs/product/MATERIAL_REQUIREMENTS.md` 与 #37 的仲裁：只导入
``review_status=已仲裁`` 的行、必交与条件交必须各自的字段齐、材料的四种状态不能混。
运行期的展开另有两条硬要求：季度类事实展开成具体季度（八个引用），条件交只有被政策
真正用到时才出现——但它没有事实键时判断不了，按「需人工核对」列出，不静默省略。
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

import pytest

from app.agents.contracts import MaterialRequirement
from app.domain.preparation import (
    CONDITIONAL_PREFIX,
    REQUIRED_PREFIX,
    STATUS_MANUAL_CHECK,
    STATUS_MISSING,
    STATUS_POLICY_CLOSED,
    STATUS_PROVIDED,
    build_items,
)
from app.domain.rules import FACT_UNITS, quarters_of
from app.materials import __main__ as cli
from app.materials.catalog import (
    COLUMNS,
    MaterialError,
    parse,
    read_policy_codes,
    read_rows,
)

PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
KEYS = tuple(FACT_UNITS)


def row(**overrides) -> dict[str, str]:
    base = {
        "policy_type": "tax",
        "material_id": "TAX-01",
        "title": "营业执照（登记地址、经营范围、成立日期）",
        "required": "必交",
        "condition_desc": "",
        "description": "用于判断企业所属地区与行业",
        "proves_fact_keys": "region;industry",
        "applies_to_policy_ids": "",
        "source_basis": "企业登记信息",
        "review_status": "已仲裁",
    }
    base.update(overrides)
    return base


def requirement(**overrides) -> MaterialRequirement:
    base = {
        "material_id": "TAX-01",
        "policy_type": "tax",
        "title": "营业执照",
        "is_required": True,
        "condition_desc": None,
        "description": "登记类材料",
        "proves_fact_keys": ("region", "industry"),
    }
    base.update(overrides)
    return MaterialRequirement(**base)


# ------------------------------------------------------------- CSV 解析与校验


def test_only_arbitrated_rows_are_importable() -> None:
    catalog = parse(
        [
            row(material_id="TAX-01"),
            row(
                material_id="TAX-04",
                required="条件交",
                condition_desc="需证明存在研发活动",
                proves_fact_keys="rdActivity",
                review_status="待核实（依据未入库）",
            ),
        ],
        fact_keys=KEYS,
    )

    assert [item.material_id for item in catalog.importable] == ["TAX-01"]
    assert catalog.skipped == (("TAX-04", "待核实（依据未入库）"),)


def test_a_skipped_row_is_not_validated_but_is_not_imported() -> None:
    # 待核实的行不进库，也就不该因为它的键还没定下来而卡住整份导入
    catalog = parse(
        [row(material_id="GRANT-09", proves_fact_keys="", review_status="待核实（依据未入库）")],
        fact_keys=KEYS,
    )

    assert catalog.importable == ()
    assert catalog.review_statuses() == {"待核实（依据未入库）": 1}


def test_required_material_cannot_carry_a_trigger_condition() -> None:
    with pytest.raises(MaterialError, match="必交材料不写 condition_desc"):
        parse([row(condition_desc="政策含门槛时")], fact_keys=KEYS)


def test_conditional_material_must_say_when_it_is_needed() -> None:
    with pytest.raises(MaterialError, match="条件交材料必须写 condition_desc"):
        parse([row(required="条件交", condition_desc="")], fact_keys=KEYS)


def test_unknown_fact_key_is_rejected_with_its_name() -> None:
    with pytest.raises(MaterialError, match="vatCredit"):
        parse([row(proves_fact_keys="region;vatCredit")], fact_keys=KEYS)


def test_the_same_material_cannot_appear_twice() -> None:
    with pytest.raises(MaterialError, match="重复"):
        parse([row(), row()], fact_keys=KEYS)


def test_material_id_shape_is_checked() -> None:
    with pytest.raises(MaterialError, match="TAX-01"):
        parse([row(material_id="税务-1")], fact_keys=KEYS)


def test_the_real_csv_only_uses_dictionary_keys() -> None:
    """M3 的数据闸门：15 行的材料键都必须落在 18 键字典里（与仲裁状态无关）。"""

    path = Path(__file__).resolve().parents[3] / "data" / "policies" / "material_requirements.csv"
    rows = read_rows(path)

    assert len(rows) == 15
    for item in rows:
        for key in (item["proves_fact_keys"] or "").split(";"):
            if key.strip():
                assert key.strip() in FACT_UNITS, item["material_id"]


def test_a_missing_column_is_reported_not_guessed(tmp_path) -> None:
    path = tmp_path / "material_requirements.csv"
    path.write_text("policy_type,material_id\n tax,TAX-01\n", encoding="utf-8")

    with pytest.raises(MaterialError, match="condition_desc"):
        read_rows(path)


def test_policy_codes_come_from_the_source_register(tmp_path) -> None:
    path = tmp_path / "source_register.csv"
    path.write_text(
        "policy_id,version\n"
        "CN-TAX-SME-2023-012,财政部 税务总局公告2023年第12号\n",
        encoding="utf-8",
    )

    assert read_policy_codes(path) == {
        "CN-TAX-SME-2023-012": ("财政部 税务总局公告2023年第12号",)
    }


def test_one_code_may_register_several_documents(tmp_path) -> None:
    """`CN-TAX-GXJS` 确实同时登记了已废止的 2008 版与现行 2016 版。

    这里保留两份，歧义只在材料真正引用这个代号时才由导入器判定——否则一份没用到的
    旧代号会卡住整份清单。
    """

    path = tmp_path / "source_register.csv"
    path.write_text(
        "policy_id,version\n"
        "CN-TAX-GXJS,国科发火〔2008〕172号\n"
        "CN-TAX-GXJS,国科发火〔2016〕32号\n",
        encoding="utf-8",
    )

    assert read_policy_codes(path) == {
        "CN-TAX-GXJS": ("国科发火〔2008〕172号", "国科发火〔2016〕32号")
    }


def test_the_real_register_keeps_both_versions_of_a_reissued_code() -> None:
    path = Path(__file__).resolve().parents[3] / "data" / "policies" / "source_register.csv"

    codes = read_policy_codes(path)

    assert len(codes["CN-TAX-GXJS"]) == 2


# ------------------------------------------------------------- 运行期展开


def test_a_required_material_is_always_listed() -> None:
    items = build_items(
        materials=[requirement()],
        referenced=[],
        known={("region", *PERIOD), ("industry", *PERIOD)},
        period=PERIOD,
        policy_closed=False,
    )

    assert len(items) == 1
    # 条件里一条都没用到，仍要列出：营业执照是登记类材料，缺的事实确实要补
    assert items[0].title.startswith(REQUIRED_PREFIX)
    assert set(items[0].fact_refs) == {("region", *PERIOD), ("industry", *PERIOD)}
    assert items[0].status == STATUS_PROVIDED


def test_a_conditional_material_without_fact_keys_is_listed_for_a_human() -> None:
    """GRANT-02「项目申请书」：条件交但键为空，判断不了何时需要，不能静默消失。"""

    items = build_items(
        materials=[
            requirement(
                material_id="GRANT-02",
                policy_type="grant",
                title="项目申请书",
                is_required=False,
                condition_desc="资金项目在线申报入口",
                proves_fact_keys=(),
            )
        ],
        referenced=[],
        known=set(),
        period=PERIOD,
        policy_closed=False,
    )

    assert len(items) == 1
    assert items[0].status == STATUS_MANUAL_CHECK
    assert items[0].missing is True
    assert items[0].title.startswith(CONDITIONAL_PREFIX)


def test_a_conditional_material_the_policy_does_not_use_is_absent() -> None:
    items = build_items(
        materials=[
            requirement(
                material_id="TAX-06",
                title="各季度从业人数统计",
                is_required=False,
                condition_desc="政策含从业人数门槛",
                proves_fact_keys=("employeeCountStart", "employeeCountEnd"),
            )
        ],
        referenced=[("taxableIncome", *PERIOD)],
        known={("taxableIncome", *PERIOD)},
        period=PERIOD,
        policy_closed=False,
    )

    assert items == []


def test_quarterly_keys_expand_into_eight_quarter_refs() -> None:
    quarters = quarters_of(PERIOD[0].year)
    starts = [("employeeCountStart", *quarter) for quarter in quarters]
    ends = [("employeeCountEnd", *quarter) for quarter in quarters]

    items = build_items(
        materials=[
            requirement(
                material_id="TAX-06",
                title="各季度从业人数统计",
                is_required=False,
                condition_desc="政策含从业人数门槛",
                proves_fact_keys=("employeeCountStart", "employeeCountEnd"),
            )
        ],
        referenced=[*starts, *ends],
        known=set(starts) | set(ends),
        period=PERIOD,
        policy_closed=False,
    )

    assert len(items) == 1
    assert len(items[0].fact_refs) == 8
    assert set(items[0].fact_refs) == set(starts) | set(ends)
    assert items[0].title.startswith(CONDITIONAL_PREFIX)


def test_a_missing_quarter_makes_the_material_missing() -> None:
    quarters = quarters_of(PERIOD[0].year)
    referenced = [("assetsStart", *quarter) for quarter in quarters] + [
        ("assetsEnd", *quarter) for quarter in quarters
    ]
    # 最后一个季度的期末还没取到，其余七个都在
    known = {ref for ref in referenced if ref not in {("assetsEnd", *quarters[-1])}}

    items = build_items(
        materials=[
            requirement(
                material_id="TAX-05",
                title="各季度资产负债表",
                is_required=False,
                condition_desc="政策含资产总额季度平均门槛",
                proves_fact_keys=("assetsStart", "assetsEnd"),
            )
        ],
        referenced=referenced,
        known=known,
        period=PERIOD,
        policy_closed=False,
    )

    assert items[0].status == STATUS_MISSING
    assert items[0].missing is True


def test_a_material_without_fact_keys_asks_a_human_instead_of_claiming_it_is_missing() -> None:
    items = build_items(
        materials=[
            requirement(
                material_id="GRANT-02",
                policy_type="grant",
                title="项目申请书",
                proves_fact_keys=(),
            )
        ],
        referenced=[],
        known=set(),
        period=PERIOD,
        policy_closed=False,
    )

    assert items[0].status == STATUS_MANUAL_CHECK
    # 口径仍是保守的：不认为已提供
    assert items[0].missing is True
    assert items[0].fact_refs == ()


def test_a_closed_policy_says_so_instead_of_listing_missing_material() -> None:
    items = build_items(
        materials=[requirement()],
        referenced=[],
        known=set(),
        period=PERIOD,
        policy_closed=True,
    )

    assert items[0].status == STATUS_POLICY_CLOSED
    # 政策关了就不存在「缺材料」：材料再齐也没处交
    assert items[0].missing is False


def test_the_trigger_condition_rides_along_in_the_description() -> None:
    items = build_items(
        materials=[
            requirement(
                material_id="TAX-02",
                title="企业所得税年度纳税申报表",
                is_required=False,
                condition_desc="政策含应纳税所得额阈值判断",
                proves_fact_keys=("taxableIncome",),
            )
        ],
        referenced=[("taxableIncome", *PERIOD)],
        known=set(),
        period=PERIOD,
        policy_closed=False,
    )

    assert items[0].description.endswith("（何时需要：政策含应纳税所得额阈值判断）")
    assert items[0].status == STATUS_MISSING


def test_items_are_ordered_by_material_id() -> None:
    items = build_items(
        materials=[
            requirement(material_id="TAX-06", proves_fact_keys=("employeeCountStart",)),
            requirement(material_id="TAX-01"),
        ],
        referenced=[("employeeCountStart", *PERIOD)],
        known=set(),
        period=PERIOD,
        policy_closed=False,
    )

    # TAX-06 是条件交，引用了才出现；顺序按代号，不按入库顺序
    assert [item.material_id for item in items] == ["TAX-01", "TAX-06"]


# ------------------------------------------------------------------ 命令行


def _write_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "material_requirements.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


def test_check_mode_reports_what_would_be_imported(tmp_path, capsys) -> None:
    path = _write_csv(tmp_path, [row()])

    assert cli.main(["check", "--csv", str(path)]) == 0

    output = capsys.readouterr().out
    assert "可导入 1 行" in output
    assert "TAX-01" in output


def test_import_is_refused_when_nothing_is_arbitrated(tmp_path, capsys) -> None:
    path = _write_csv(tmp_path, [row(material_id="TAX-04", review_status="待核实（依据未入库）")])

    assert cli.main(["import", "--csv", str(path)]) == 2

    assert "没有已仲裁的行" in capsys.readouterr().err


def test_a_bad_csv_exits_with_the_reason(tmp_path, capsys) -> None:
    path = _write_csv(tmp_path, [row(proves_fact_keys="vatCredit")])

    assert cli.main(["check", "--csv", str(path)]) == 2

    assert "vatCredit" in capsys.readouterr().err
