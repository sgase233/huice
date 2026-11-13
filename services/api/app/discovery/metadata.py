"""候选元数据的保守启发式：只摘原文里写着的字段，摘不到的留空。

# 为什么「摘不到就留空」是硬要求

政策候选要由审核员确认后才能提交（#47）。抓取器如果按常识补一个「看起来合理」的
数值——比如把页脚日期当成发布日期、把栏目名当成机关、按域名猜区划——审核员看到的
就是一条**看似完整、实际无据**的候选，比字段空着更难发现。所以这里每一条规则都要求
「原文里有一段能直接引用的字样」，抽不到就是 ``None``，并进 ``missing_fields``。

# 特别不做的几件事

* **不猜版本号**：``version`` 由审核员按登记表口径命名，抓取器没有依据。
* **不猜适用主体**：``applicableEntities`` 需要业务判断，一律留给审核员。
* **不从页码/页脚推断成文日期**：政府站点的页脚版权年、最后更新日期与成文日期不是
  一回事（S01/S06/S07 样本里两者都不同）。只认「成文日期 / 发布日期 / 印发日期」
  这类**带标签**的日期；没标签就不填。
* **区划只按域名给**：这张表来自 ``data/policies/source_register.csv`` 已核定的
  国家级/深圳入口，是把「哪个站点」翻译成它登记用的区划码，不是从正文猜地名。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

#: 标题里出现这些词才算「这是一条政策」，避免把导航文字、"当前位置："当成标题。
_TITLE_KEYWORDS = (
    "通知",
    "公告",
    "通告",
    "办法",
    "意见",
    "规定",
    "细则",
    "方案",
    "措施",
    "决定",
    "批复",
    "指南",
    "规则",
    "计划",
    "标准",
)

#: 标题里没有关键词时的二次判据：《……》书名号标题（多为规章、办法）。
_TITLE_MAX_LEN = 120
_TITLE_MIN_LEN = 6

_ISSUER_SUFFIXES = (
    "部",
    "委",
    "局",
    "厅",
    "署",
    "院",
    "办公室",
    "政府",
    "委员会",
    "总局",
    "管理中心",
)
_ISSUER_MAX_LEN = 40

_DOC_NUMBER_RE = re.compile(
    r"[一-鿿]{1,12}[〔\[［(（]\s*\d{4}\s*[〕\]］)）]\s*\d{1,4}\s*号"
)
_LABELED_DATE_RE = re.compile(
    r"(?:成文日期|发布日期|印发日期|发文日期|信息提供日期|发布时间)\s*[:：]?\s*"
    r"(\d{4})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})\s*日?"
)
_EFFECTIVE_RE = re.compile(
    r"自\s*(\d{4})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})\s*日?\s*起?"
    r"[^。；\n]{0,12}(?:施行|实施|执行|生效)"
)

#: 官方入口主机 → 登记表使用的区划码。来源：``data/policies/source_register.csv``
#: 的 ``region`` 列（CN = 全国性，440300 = 深圳）。表外主机一律留空。
_HOST_REGIONS = {
    "www.gov.cn": "CN",
    "www.most.gov.cn": "CN",
    "www.chinatax.gov.cn": "CN",
    "www.sz.gov.cn": "440300",
}

#: 提交草稿时必须有值的字段（契约 ``PolicyImportMetadata`` 的必填项）。
#: 顺序即前端展示顺序；``sourceUrl`` 来自取证字段，不在待补之列。
REQUIRED_FIELDS = (
    "version",
    "title",
    "type",
    "issuer",
    "region",
    "applicableEntities",
    "publishedAt",
    "effectiveFrom",
    "officialEntryUrl",
)

#: 提交必填字段 → 候选表列名。**唯一**一份映射：抽取时算「还缺什么」与接口里复核
#: 都用它。之前两边各写一份（一侧 camelCase、一侧 snake_case），抽取侧因此把已经
#: 抽到的 ``publishedAt`` / ``effectiveFrom`` / ``officialEntryUrl`` 也报成缺失，
#: 审核员看到的待补清单与库里的值对不上。
FIELD_COLUMNS = {
    "version": "version",
    "title": "title",
    "type": "policy_type",
    "issuer": "issuer",
    "region": "region",
    "applicableEntities": "applicable_entities",
    "publishedAt": "published_at",
    "effectiveFrom": "effective_from",
    "officialEntryUrl": "official_entry_url",
}

def missing_fields_of(values: dict[str, object]) -> list[str]:
    """按候选行（或「行 + 人工确认的元数据」）算出还缺哪些必填字段。

    只看值，不看来源：同一份映射用在抽取结果与候选行上，审核员补完之后这两处会一致。
    ``version`` 与 ``applicableEntities`` 在抽取结果里**根本没有对应的值**
    （:meth:`ExtractedMetadata.as_columns` 不含这两列），所以必然留在这里——那不是
    这个函数的特例，是「抓取器不代填」的直接结果。
    """

    return [
        field for field in REQUIRED_FIELDS if not values.get(FIELD_COLUMNS[field])
    ]


@dataclass(frozen=True, slots=True)
class ExtractedMetadata:
    """候选阶段能确认的元数据；``None`` 表示原文没写，留给审核员。"""

    title: str | None = None
    policy_type: str | None = None
    issuer: str | None = None
    region: str | None = None
    document_number: str | None = None
    published_at: date | None = None
    effective_from: date | None = None
    official_entry_url: str | None = None

    def as_columns(self) -> dict[str, object]:
        return {
            "title": self.title,
            "policy_type": self.policy_type,
            "issuer": self.issuer,
            "region": self.region,
            "document_number": self.document_number,
            "published_at": self.published_at,
            "effective_from": self.effective_from,
            "official_entry_url": self.official_entry_url,
        }

    def missing_fields(self) -> list[str]:
        """还缺哪些提交必填字段。

        ``version`` 与 ``applicableEntities`` 永远在这里：抓取器不替人做这两个判断。
        """

        return missing_fields_of(self.as_columns())


def extract_metadata(text: str, *, source_url: str) -> ExtractedMetadata:
    """从规范化正文里摘元数据。耗时是线性的，只看前若干行。"""

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    title = _find_title(lines)
    return ExtractedMetadata(
        title=title,
        policy_type=_policy_type(title),
        issuer=_find_issuer(title, lines),
        region=_region_of(source_url),
        document_number=_find_document_number(text),
        published_at=_find_published_at(lines),
        effective_from=_find_effective_from(text),
        # 候选的来源页本身就是官方入口；这不是猜，是把取证字段带进提交体。
        official_entry_url=source_url or None,
    )


def _find_title(lines: list[str]) -> str | None:
    for line in lines[:20]:
        if not (_TITLE_MIN_LEN <= len(line) <= _TITLE_MAX_LEN):
            continue
        if line.endswith(("。", "；", "，", ":")):
            continue
        if any(keyword in line for keyword in _TITLE_KEYWORDS):
            return line
        if line.startswith("《") and line.endswith("》"):
            return line
    return None


def _policy_type(title: str | None) -> str | None:
    """类型只看标题：正文里同时出现「税」和「资金」的政策很常见，按标题判更稳。"""

    if not title:
        return None
    if "税" in title:
        return "tax"
    if any(word in title for word in ("申报", "补助", "补贴", "资助", "扶持", "奖励", "资金")):
        return "grant"
    return None


def _find_issuer(title: str | None, lines: list[str]) -> str | None:
    """机关优先从标题的「……关于」前缀取，其次找落款性质的一行。

    ``市工业和信息化局关于…`` 这种标题不补地名：原文写什么就记什么，区划另由
    入口域名给出，两者都不猜。
    """

    if title and "关于" in title:
        prefix = title.split("关于", 1)[0].strip()
        if 2 <= len(prefix) <= _ISSUER_MAX_LEN and any(
            suffix in prefix for suffix in _ISSUER_SUFFIXES
        ):
            return prefix

    for line in lines[:30]:
        if len(line) > _ISSUER_MAX_LEN or line.endswith(("。", "；", "，", ":", "：")):
            continue
        if line.startswith(("根据", "按照", "依据", "为", "现将")):
            continue
        if any(line.endswith(suffix) for suffix in _ISSUER_SUFFIXES):
            return line
    return None


def _region_of(source_url: str) -> str | None:
    host = (urlsplit(source_url).hostname or "").lower()
    return _HOST_REGIONS.get(host)


def _find_document_number(text: str) -> str | None:
    match = _DOC_NUMBER_RE.search(text)
    if match is None:
        return None
    return re.sub(r"\s+", "", match.group(0))


def _find_published_at(lines: list[str]) -> date | None:
    head = "\n".join(lines[:40])
    for match in _LABELED_DATE_RE.finditer(head):
        parsed = _safe_date(match.group(1), match.group(2), match.group(3))
        if parsed is not None:
            return parsed
    return None


def _find_effective_from(text: str) -> date | None:
    match = _EFFECTIVE_RE.search(text)
    if match is None:
        return None
    return _safe_date(match.group(1), match.group(2), match.group(3))


def _safe_date(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None
