"""候选元数据抽取的单元用例：「只摘原文写着的，摘不到就留空」。

这些用例守着一条产品要求（#47）：审核员看到的候选不能是「看起来完整、实际无据」的。
所以每条断言都在证明**没有**发生推断——版本号、适用主体、区划、成文日期都不会被猜出来。

另有一条正例锁住标题关键词表：「关于……的若干措施」是最常见的政策标题格式，漏掉它就
等于让这类页面的标题一直是空的。
"""

from __future__ import annotations

from datetime import date

from app.discovery.metadata import REQUIRED_FIELDS, extract_metadata

NEWS_URL = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
SHENZHEN_URL = "https://www.sz.gov.cn/zfgb/2026/1/content_2.htm"
TAX_URL = "https://www.chinatax.gov.cn/chinatax/n810341/n810755/c1/content.html"


def test_title_ending_in_measures_is_recognized():
    meta = extract_metadata(
        "关于进一步支持小微企业发展的若干措施\n国办发〔2026〕12号\n", source_url=NEWS_URL
    )
    assert meta.title == "关于进一步支持小微企业发展的若干措施"
    assert meta.document_number == "国办发〔2026〕12号"


def test_navigation_text_is_not_mistaken_for_a_title():
    text = "当前位置：首页 > 政策 > 最新文件\n2026-11-12\n正文内容略。\n"
    meta = extract_metadata(text, source_url=NEWS_URL)
    assert meta.title is None
    assert meta.policy_type is None


def test_book_title_marker_is_the_second_judgement():
    meta = extract_metadata(
        "《深圳市市级财政专项资金管理办法》\n深圳市财政局\n", source_url=SHENZHEN_URL
    )
    assert meta.title == "《深圳市市级财政专项资金管理办法》"


def test_version_and_applicable_entities_are_never_guessed():
    meta = extract_metadata(
        "关于增值税优惠政策的通知\n财政部\n发布日期：2026年11月10日\n", source_url=TAX_URL
    )
    missing = meta.missing_fields()
    # 版本号与适用主体只能由审核员给：原文里没有依据，抓取器一律不填。
    assert "version" in missing
    assert "applicableEntities" in missing
    assert set(missing) <= set(REQUIRED_FIELDS)


def test_region_comes_from_the_registered_host_only():
    assert extract_metadata("正文", source_url=NEWS_URL).region == "CN"
    assert extract_metadata("正文", source_url=SHENZHEN_URL).region == "440300"
    # 表外主机不猜：宁可留空进 missingFields，也不按域名编一个区划码。
    assert extract_metadata("正文", source_url="https://example.gov.cn/a.htm").region is None


def test_policy_type_is_read_from_the_title_only():
    assert extract_metadata("关于增值税优惠政策的通知\n", source_url=TAX_URL).policy_type == "tax"
    assert (
        extract_metadata("关于下达专项资金预算的通知\n", source_url=TAX_URL).policy_type
        == "grant"
    )
    assert extract_metadata("关于开展安全检查的通告\n", source_url=NEWS_URL).policy_type is None


def test_issuer_is_taken_from_the_about_phrase():
    meta = extract_metadata("财政部关于下达2026年补贴预算的通知\n", source_url=TAX_URL)
    assert meta.issuer == "财政部"


def test_published_at_requires_a_label():
    labeled = extract_metadata(
        "关于增值税优惠政策的通知\n发布日期：2026年11月10日\n", source_url=TAX_URL
    )
    assert labeled.published_at == date(2026, 11, 10)

    # 页脚版权年、无标签的裸日期都不算成文日期——这两者与成文日期常常不是同一个。
    footer = extract_metadata(
        "关于增值税优惠政策的通知\n2026-10-01\n版权所有 2026 国家税务总局\n", source_url=TAX_URL
    )
    assert footer.published_at is None


def test_effective_from_requires_an_explicit_self_start_phrase():
    text = "关于增值税优惠政策的通知\n本办法自2026年12月1日起施行。\n"
    assert extract_metadata(text, source_url=TAX_URL).effective_from == date(2026, 12, 1)

    vague = "关于增值税优惠政策的通知\n本办法2026年12月1日印发。\n"
    assert extract_metadata(vague, source_url=TAX_URL).effective_from is None


def test_the_official_entry_url_is_the_source_page_itself():
    meta = extract_metadata("关于增值税优惠政策的通知\n", source_url=TAX_URL)
    assert meta.official_entry_url == TAX_URL


def test_a_full_page_reports_no_missing_field_it_can_confirm():
    meta = extract_metadata(
        "关于增值税优惠政策的通知\n"
        "财政部\n"
        "发布日期：2026年11月10日\n"
        "自2026年12月1日起施行\n",
        source_url=TAX_URL,
    )
    # 只剩「抓取器确实不该填」的两项：版本号与适用主体。
    assert meta.missing_fields() == ["version", "applicableEntities"]
