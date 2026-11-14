"""``infra/deploy/backfill-date-provenance.py``：数据文件校验与前置守卫（不连库）。

脚本的价值全在「写库之前把不合法的回填挡下来」——``verified`` 没有出处、``derived``
没有推算依据、出处指向不存在的附件序号，这些错误一旦写进 ``policy_version`` 就是审计
表里的假证据。这里逐条锁住这些守卫，以及「缺 ``DATABASE_URL`` 只报变量名」「数据不
合法时不连库」两条行为。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "backfill-date-provenance.py"
DATA = REPO_ROOT / "infra" / "deploy" / "date-provenance-backfill.json"
SOURCE_URL = "https://example.gov.cn/notice/1.html"


def _load_script():
    spec = importlib.util.spec_from_file_location("backfill_date_provenance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("backfill_date_provenance", module)
    spec.loader.exec_module(module)
    return module


backfill = _load_script()


def entry(**provenance) -> dict:
    return {
        "sourceUrl": SOURCE_URL,
        "version": "测试〔2026〕1号",
        "provenance": {"version": 1, **provenance},
    }


#: 批准时随状态一起交的复核记录：谁、什么时候、依据哪份记录。
EVIDENCE = {
    "reviewedBy": "wlj80176-creator",
    "reviewedAt": "2026-11-14",
    "reference": "https://github.com/sgase233/huice/pull/69#issuecomment-1",
}


def write(
    tmp_path: Path,
    entries: list[dict],
    review_status: str = "approved-by-m3",
    review_evidence: dict | None = EVIDENCE,
) -> Path:
    payload: dict = {"version": 1, "reviewStatus": review_status, "entries": entries}
    if review_evidence is not None:
        payload["reviewEvidence"] = review_evidence
    path = tmp_path / "data.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_shipped_data_file_is_valid() -> None:
    """仓库里那份回填数据自身必须过校验，否则部署时才发现就晚了。"""
    payload = backfill.load_data(DATA)
    # 按政策版本计：8 个 policy_id，GXJS 有 2008/2016 两版，共 9 条
    assert len(payload.entries) == 9
    keys = {(entry.source_url, entry.version) for entry in payload.entries}
    assert len(keys) == len(payload.entries)
    versions = {entry.version for entry in payload.entries}
    # M3 复核 F1：登记的 9 个版本一个都不能漏，漏了就是某版本没有来源记录
    assert versions == {
        "深工信规〔2025〕4号",
        "深工信规〔2024〕12号",
        "深工信规〔2025〕8号",
        "深科创规〔2026〕1号",
        "深科技创新规〔2021〕5号",
        "财政部 税务总局公告2023年第7号",
        "财政部 税务总局公告2023年第12号",
        "国科发火〔2008〕172号",
        "国科发火〔2016〕32号",
    }


def test_shipped_data_is_approved_with_evidence() -> None:
    """M3 于 2026-11-14 批准：状态是批准值，且带着指向批准评论的复核证据。"""

    payload = backfill.load_data(DATA)
    assert payload.review_status == "approved-by-m3"
    assert payload.review_evidence is not None
    assert payload.review_evidence["reviewedBy"] == "wlj80176-creator"
    assert payload.review_evidence["reviewedAt"] == "2026-11-14"
    # reference 必须是那条批准评论，不能是 PR 主页面
    assert "/pull/69#issuecomment-" in payload.review_evidence["reference"]
    assert backfill.review_block(payload, apply=True) is None


def test_shipped_entries_never_treat_window_as_validity() -> None:
    """评审点名的口径：SMART／GREEN 的申报窗口不能充当政策有效期。"""
    entries = {entry.version: entry for entry in backfill.load_data(DATA).entries}
    smart = entries["深工信规〔2025〕4号"].provenance
    assert smart["applicationWindow"]["status"] == "verified"
    assert smart["effectiveFrom"]["status"] == "unverified"
    # 版本行的 effective_to 置空后，没有值 → unknown（不是 unverified）
    assert smart["effectiveTo"]["status"] == "unknown"
    assert "不是政策有效期" in smart["note"]

    green = entries["深工信规〔2024〕12号"].provenance
    assert green["effectiveTo"]["status"] == "unknown"
    # note 不能再写「登记值等于申报窗口」——版本行已经是占位值
    assert "登记值等于申报窗口" not in green["note"]
    assert "published_at / null 占位" in green["note"]


def test_tax_sme_locators_point_at_the_clause_that_holds_the_date() -> None:
    """M3 复核 P3：日期在公告第一、二条，截止日还要看第三条（2027-12-31）。"""

    entries = {entry.version: entry for entry in backfill.load_data(DATA).entries}
    tax = entries["财政部 税务总局公告2023年第12号"].provenance
    assert tax["effectiveFrom"]["locator"] == "公告第一、二条"
    assert tax["effectiveTo"]["locator"] == "公告第一、二、三条"


def test_shipped_attachment_ordinals_are_one_based() -> None:
    """数据里写的是「第几份附件」：从 1 起。写 0 会被当成正文那行，整批回填失败。"""
    for entry in backfill.load_data(DATA).entries:
        for name, spec in entry.sources.items():
            if spec["kind"] == "attachment":
                assert spec["ordinal"] >= 1, f"{entry.version} 的 {name} 序号非法"
    assert "两批受理，办理方式分网络填报与书面材料两种" in {
        entry.version: entry for entry in backfill.load_data(DATA).entries
    }["深工信规〔2025〕4号"].provenance["note"]


def test_verified_requires_locator() -> None:
    raw = entry(effectiveFrom={"status": "verified", "source": {"kind": "body"}})
    with pytest.raises(backfill.DataError, match=r"entries\[0\]\.provenance\.effectiveFrom\.locator"):
        backfill.validate_entry(0, raw)


def test_verified_requires_source() -> None:
    raw = entry(effectiveFrom={"status": "verified", "locator": "第一条"})
    with pytest.raises(backfill.DataError, match=r"\.source"):
        backfill.validate_entry(0, raw)


def test_body_source_rejects_ordinal() -> None:
    raw = entry(
        applicationWindow={
            "status": "verified",
            "locator": "第七条",
            "source": {"kind": "body", "ordinal": 0},
        }
    )
    with pytest.raises(backfill.DataError, match="正文不写序号"):
        backfill.validate_entry(0, raw)


def test_attachment_source_requires_ordinal() -> None:
    raw = entry(
        applicationWindow={
            "status": "verified",
            "locator": "附件 1",
            "source": {"kind": "attachment"},
        }
    )
    with pytest.raises(backfill.DataError, match="附件序号从 1 起"):
        backfill.validate_entry(0, raw)


@pytest.mark.parametrize("ordinal", [0, -1, True, 1.0])
def test_attachment_ordinal_must_be_one_based_int(ordinal) -> None:
    """0 是最容易犯的错：库里 0 是正文，写成 0 会指向正文那行。"""
    raw = entry(
        applicationWindow={
            "status": "verified",
            "locator": "附件 1",
            "source": {"kind": "attachment", "ordinal": ordinal},
        }
    )
    with pytest.raises(backfill.DataError, match="附件序号从 1 起"):
        backfill.validate_entry(0, raw)


def test_derived_requires_basis() -> None:
    raw = entry(effectiveTo={"status": "derived", "derivedFrom": "2026-02-24"})
    with pytest.raises(backfill.DataError, match=r"\.basis"):
        backfill.validate_entry(0, raw)


def test_derived_from_must_be_iso_date() -> None:
    raw = entry(effectiveTo={"status": "derived", "basis": "施行 5 年", "derivedFrom": "2026/02/24"})
    with pytest.raises(backfill.DataError, match="合法的 YYYY-MM-DD"):
        backfill.validate_entry(0, raw)


def test_unknown_status_rejected() -> None:
    raw = entry(effectiveTo={"status": "maybe"})
    with pytest.raises(backfill.DataError, match="只能是"):
        backfill.validate_entry(0, raw)


def test_status_cannot_carry_foreign_fields() -> None:
    """``unknown`` 不该带推算依据：状态与字段必须一致，否则读的人会误判。"""
    raw = entry(effectiveTo={"status": "unknown", "basis": "公告未写"})
    with pytest.raises(backfill.DataError, match="不允许字段"):
        backfill.validate_entry(0, raw)


def test_empty_provenance_rejected() -> None:
    raw = entry()
    with pytest.raises(backfill.DataError, match="至少要给一个日期字段"):
        backfill.validate_entry(0, raw)


def test_note_alone_is_allowed() -> None:
    stored = backfill.validate_entry(0, entry(note="两批受理，批次见附件")).provenance
    assert stored == {"version": 1, "note": "两批受理，批次见附件"}


def test_duplicate_entries_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, [entry(note="a"), entry(note="b")])
    with pytest.raises(backfill.DataError, match="与其他条目重复"):
        backfill.load_data(path)


def test_missing_review_status_rejected(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text(
        json.dumps({"version": 1, "entries": [entry(note="a")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(backfill.DataError, match="reviewStatus"):
        backfill.load_data(path)


def test_approved_status_with_evidence_unblocks_apply() -> None:
    payload = backfill.load_data(DATA)
    approved = backfill.Payload(
        review_status="approved-by-m3", entries=payload.entries, review_evidence=dict(EVIDENCE)
    )
    assert backfill.review_block(approved, apply=True) is None


def test_approved_status_alone_does_not_unblock_apply() -> None:
    """只把状态改成批准值、不给复核记录：--apply 照样拒绝。

    审计表里「approved-by-m3」这句得能追到人和记录，否则和没复核过一样。
    """
    payload = backfill.load_data(DATA)
    approved = backfill.Payload(review_status="approved-by-m3", entries=payload.entries)
    blocked = backfill.review_block(approved, apply=True)
    assert blocked is not None
    assert "reviewEvidence" in blocked
    # 干跑不受影响：拿不到批准证据时仍要能看匹配结果
    assert backfill.review_block(approved, apply=False) is None


@pytest.mark.parametrize(
    "evidence, message",
    [
        ({"reviewedBy": "m3", "reviewedAt": "2026-11-14"}, r"reviewEvidence\.reference"),
        ({"reviewedAt": "2026-11-14", "reference": "PR #69"}, r"reviewEvidence\.reviewedBy"),
        ({"reviewedBy": "m3", "reference": "PR #69"}, r"reviewEvidence\.reviewedAt"),
        ({"reviewedBy": " ", "reviewedAt": "2026-11-14", "reference": "PR #69"}, r"reviewedBy"),
        ({"reviewedBy": "m3", "reviewedAt": "2026/11/14", "reference": "PR #69"}, "YYYY-MM-DD"),
        (
            {"reviewedBy": "m3", "reviewedAt": "2026-11-14", "reference": "PR #69", "note": "x"},
            "不允许字段",
        ),
    ],
)
def test_review_evidence_must_be_complete(tmp_path: Path, evidence: dict, message: str) -> None:
    path = write(tmp_path, [entry(note="a")], review_evidence=evidence)
    with pytest.raises(backfill.DataError, match=message):
        backfill.load_data(path)


def test_review_evidence_does_not_make_a_draft_approved(tmp_path: Path) -> None:
    """证据是批准的必要条件，不是充分条件：reviewStatus 仍是草案就只许干跑。"""
    path = write(tmp_path, [entry(note="a")], review_status="draft-pending-m3")
    payload = backfill.load_data(path)
    assert payload.review_evidence == EVIDENCE
    assert backfill.review_block(payload, apply=True) is not None


def test_missing_database_url_reports_variable_only(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert backfill.run(DATA, apply=False) == 2
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert "postgresql://" not in err


def test_invalid_data_exits_2_without_touching_database(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@127.0.0.1:1/none")
    path = write(tmp_path, [entry(effectiveTo={"status": "derived"})])
    assert backfill.main(["--data", str(path)]) == 2
    err = capsys.readouterr().err
    assert "entries[0].provenance.effectiveTo.basis" in err
    # 校验先于连库：数据不合法时不该出现任何数据库错误，更不该回显连接串
    assert "secret" not in err
    assert "数据库操作失败" not in err
