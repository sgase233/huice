"""消融对照的输入：把 `data/` 下的四份文件拼成可以直接跑的判断用例。

口径（2026-11-06 定稿，见 ``docs/IMPLEMENTATION_AND_INTERFACE.md`` §2.10）：

* 一个 case = 一家企业 × 一项政策的**一个文号**。``CN-TAX-GXJS`` 的新旧两个文号
  各算一个，所以是 **34** 个 case 而不是「6 企业 × 6 政策」的 32 对。
* ``国科发火〔2008〕172号`` 的两个 case（``C031``/``C033``，共 2 条标签）**不进分母**：
  判断接口按 ``asOf`` 选版本，2026-11-06 只会解析到 2016 版；要复现 2008 版只能把
  ``asOf`` 设进 2015 窗口，而企业事实的期间是 2026 年——那样两个方案都会因「没有事实」
  得到 ``insufficient_evidence``，等于靠缺数据白拿一致。它们单列在 ``excluded`` 里。
* 标签只有三类（``satisfied`` / ``insufficient_evidence`` / ``not_satisfied``）；
  契约里的 ``conflict`` 不是标注值，基线若输出 conflict 计为不一致，并在指标里单列条数。

本模块只读文件、不连库，便于在无数据库的机器上跑通基线与指标计算。
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..domain.rules import FACT_UNITS

#: 仓库根目录（``services/api/app/ablation/dataset.py`` → 上四级）。
REPO_ROOT = Path(__file__).resolve().parents[4]

#: 标注值域。``conflict`` 在契约里有、在标注里没有，故意不在这一组。
LABEL_RESULTS = ("satisfied", "not_satisfied", "insufficient_evidence")

#: 不进分母的政策版本与理由。键是 (政策代号, 文号)。
EXCLUDED_VERSIONS: Mapping[tuple[str, str], str] = {
    ("CN-TAX-GXJS", "国科发火〔2008〕172号"): "该版本已于 2015-12-31 失效，接口按 asOf 只解析到 2016 版",
}

REGISTER_COLUMNS = (
    "policy_id",
    "title",
    "type",
    "issuer",
    "region",
    "source_url",
    "version",
    "published_at",
    "effective_from",
    "effective_to",
    "application_open",
    "application_close",
    "status",
)
CRITERIA_COLUMNS = (
    "policy_id",
    "criterion_id",
    "source_version",
    "source_span",
    "source_page_or_section",
    "adjudicated_expression",
    "review_status",
)
ENTERPRISE_COLUMNS = (
    "enterprise_id",
    "name_synthetic",
    "region",
    "industry",
    "fact_key",
    "fact_value",
    "unit",
    "period",
    "source",
    "verified",
)
LABEL_COLUMNS = (
    "case_id",
    "enterprise_id",
    "policy_id",
    "policy_version",
    "criterion_id",
    "adjudicated_result",
    "split",
)


class DatasetError(Exception):
    """输入文件对不上口径时抛出；消息里点名文件、行与代号，不猜。"""


def normalize_for_match(text: str) -> str:
    """逐字比对前的归一化：NFKC（全角括号数字转半角）再去掉全部空白。"""

    return "".join(unicodedata.normalize("NFKC", text).split())


@dataclass(frozen=True, slots=True)
class Policy:
    code: str
    document: str
    title: str
    policy_type: str
    issuer: str
    region: str
    source_url: str
    published_at: date
    effective_from: date
    effective_to: date | None
    application_open: date | None
    application_close: date | None
    status: str

    @property
    def expired(self) -> bool:
        """文号本身是否已过适用期；判断当天的日期由调用方给，不在数据里写死。"""

        return self.effective_to is not None


@dataclass(frozen=True, slots=True)
class Criterion:
    """一条已仲裁条件：标注代号、原文片段与仲裁后的 AST。

    ``documents`` 与 ``spans`` **逐段对齐**，每段写明该段引文出自哪份文件；空串表示
    该段出自政策本体。出处不一定是政策版本的文号：10 条条件的出处是另一份文件
    （``财税〔2015〕119号``、深圳市 2025 年专精特新申报通知），所以按
    (政策, 条件代号) 建键，不拿出处当版本号用。

    这一列以前读的是 ``source_version``——那时 ``source_version`` 写的就是「引文出自
    哪份文件」。它改为「条件归属哪个政策版本」之后，两者不再是一回事，再拿它当出处
    会让校验去错误的正文里找引文（M2 在 #36 复审指出）。
    """

    code: str
    policy_code: str
    source_span: str
    section: str
    expression: dict[str, Any]
    #: 与 ``spans`` 逐段对齐的出处文件；整列留空时退回 ``version_document``。
    documents: tuple[str, ...] = ()
    #: ``source_version``：这条条件归属的政策版本文号。``source_document`` 没写时拿它当出处
    #: ——旧数据里这一列写的正是「引文出自哪份文件」，两者恰好一致。
    version_document: str = ""

    @property
    def spans(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.source_span.split("|") if part.strip())

    @property
    def document(self) -> str:
        """主出处：第一段的文件名。都为空时返回空串，表示按政策本体处理。"""

        return next((name for name in self.documents if name), self.version_document)


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    value: str | None
    unit: str
    period_start: date
    period_end: date
    source: str
    verified: bool


@dataclass(frozen=True, slots=True)
class Enterprise:
    code: str
    name: str
    region: str
    industry: str
    facts: tuple[Fact, ...]


@dataclass(frozen=True, slots=True)
class Label:
    case_id: str
    enterprise: str
    policy_code: str
    document: str
    criterion_code: str
    result: str
    split: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.case_id, self.criterion_code)


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    enterprise: str
    policy_code: str
    document: str
    labels: tuple[Label, ...]

    @property
    def criterion_codes(self) -> tuple[str, ...]:
        return tuple(label.criterion_code for label in self.labels)


@dataclass(frozen=True, slots=True)
class Dataset:
    data_dir: Path
    policies: Mapping[tuple[str, str], Policy]
    criteria: Mapping[tuple[str, str], Criterion]
    enterprises: Mapping[str, Enterprise]
    cases: tuple[Case, ...]
    excluded: tuple[Case, ...]

    #: 进分母的标签数 = 所有 case 的标签数（``excluded`` 已排除在外）。
    def scored_labels(self) -> tuple[Label, ...]:
        return tuple(label for case in self.cases for label in case.labels)

    def excluded_labels(self) -> tuple[Label, ...]:
        return tuple(label for case in self.excluded for label in case.labels)

    def labels_by_split(self, split: str) -> tuple[Label, ...]:
        return tuple(label for label in self.scored_labels() if label.split == split)

    def case_for(self, case_id: str) -> Case:
        for case in (*self.cases, *self.excluded):
            if case.case_id == case_id:
                return case
        raise DatasetError(f"没有 {case_id} 这个 case")

    def criterion(self, case: Case, code: str) -> Criterion:
        return self.criteria[(case.policy_code, code)]

    def _text_for(self, *names: str) -> str | None:
        for name in names:
            path = self.data_dir / "policies" / "sources" / f"{name}.txt"
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        return None

    def source_text(self, policy: Policy) -> str:
        """政策正文。文件按文号命名，五项政策没有同名文件时退回按政策代号命名。"""

        for name in (policy.document, policy.code):
            path = self.data_dir / "policies" / "sources" / f"{name}.txt"
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if len(text) < 200:
                    raise DatasetError(
                        f"{path.name} 只有 {len(text)} 字，是节选文件，不能当政策正文"
                    )
                return text
        raise DatasetError(
            f"政策 {policy.code}（{policy.document}）在 sources/ 下找不到正文："
            f"既不叫 {policy.document}.txt，也不叫 {policy.code}.txt"
        )

    def _policy_body(self, criterion: Criterion) -> str:
        """政策正文。条件没写出处时用它做逐字比对。

        出处是申报通知、上位法规这类**没有单独文件**的文本时也退回这里：
        专精特新 8 条引自 2025 年申报通知，而通知的条件与规程正文逐字一致。
        """

        for (code, _), policy in self.policies.items():
            if code == criterion.policy_code:
                return self.source_text(policy)
        raise DatasetError(
            f"条件 {criterion.code} 的政策 {criterion.policy_code} 不在 source_register.csv 里"
        )

    def span_source(self, criterion: Criterion, index: int) -> str:
        """第 ``index`` 段引文的出处原文。

        出处可能是另一份文件（申报通知、上位法规），也可能是 sources/ 下的节选
        （``…（节选，供引文逐字校验，非全文）``）。两者都能用来做逐字比对。
        """

        name = (
            criterion.documents[index]
            if index < len(criterion.documents)
            else criterion.version_document
        )
        if name:
            text = self._text_for(name)
            if text is not None:
                return text
        return self._policy_body(criterion)

    def verification_text(self, criterion: Criterion) -> str:
        """这条条件可用的全部出处原文（多段出处取并集）。

        用来回答「这句话有没有出处」这类整体问题；逐段校验走 ``span_source``。
        """

        texts: list[str] = []
        for index in range(len(criterion.spans)):
            text = self.span_source(criterion, index)
            if text not in texts:
                texts.append(text)
        return "\n".join(texts) if texts else self._policy_body(criterion)

    def unverifiable_criteria(self) -> frozenset[tuple[str, str]]:
        """引用在本仓可用原文里找不到的条件——这些条件的引用不进幻觉率分母。

        把「本仓没有原文」算成模型幻觉，等于把资料缺口记到方案头上。

        逐段比对：多段引文各自对各自的出处，不能拿其中一份文件的正文糊弄另一段。
        归属改对之后本仓没有缺口（``RD-001`` 的八段引文在 2023 年第7号第四条里逐字
        可查，``HT-007`` 的引文出自 119号 节选），这个集合为空；它仍是分母的判据，
        数据再改动时会自己变。
        """

        bad: set[tuple[str, str]] = set()
        for criterion in self.criteria.values():
            for index, span in enumerate(criterion.spans):
                try:
                    text = normalize_for_match(self.span_source(criterion, index))
                except DatasetError:
                    bad.add((criterion.policy_code, criterion.code))
                    break
                if normalize_for_match(span) not in text:
                    bad.add((criterion.policy_code, criterion.code))
                    break
        return frozenset(bad)

    def manifest(self) -> dict[str, str]:
        """输入文件的 sha256，用来证明两个方案跑的是同一份数据。"""

        import hashlib

        files = [
            self.data_dir / "evaluation" / "labels.csv",
            self.data_dir / "policies" / "criteria_review.csv",
            self.data_dir / "fixtures" / "enterprises.csv",
            self.data_dir / "policies" / "source_register.csv",
        ]
        return {path.relative_to(self.data_dir).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest() for path in files}


# ------------------------------------------------------------------ 读文件


def _read_rows(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise DatasetError(f"缺少输入文件：{path}")
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    missing = [name for name in columns if name not in header]
    if missing:
        raise DatasetError(f"{path.name} 缺少列：{'、'.join(missing)}")
    return [
        {(key or "").strip(): (value or "").strip() for key, value in row.items()}
        for row in reader
    ]


def _date(text: str, *, where: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DatasetError(f"{where} 的日期「{text}」不是 YYYY-MM-DD") from exc


def _optional_date(text: str, *, where: str) -> date | None:
    return _date(text, where=where) if text else None


def _split_period(text: str, *, where: str) -> tuple[date, date]:
    if "/" not in text:
        raise DatasetError(f"{where} 的期间「{text}」不是 起/止 两段")
    start, _, end = text.partition("/")
    return _date(start, where=where), _date(end, where=where)


def load_policies(data_dir: Path) -> dict[tuple[str, str], Policy]:
    rows = _read_rows(data_dir / "policies" / "source_register.csv", REGISTER_COLUMNS)
    policies: dict[tuple[str, str], Policy] = {}
    for row in rows:
        where = f"source_register.csv 的 {row['policy_id']} {row['version']}"
        key = (row["policy_id"], row["version"])
        if key in policies:
            raise DatasetError(f"{where} 重复登记")
        policies[key] = Policy(
            code=row["policy_id"],
            document=row["version"],
            title=row["title"],
            policy_type=row["type"],
            issuer=row["issuer"],
            region=row["region"],
            source_url=row["source_url"],
            published_at=_date(row["published_at"], where=where),
            effective_from=_date(row["effective_from"], where=where),
            effective_to=_optional_date(row["effective_to"], where=where),
            application_open=_optional_date(row["application_open"], where=where),
            application_close=_optional_date(row["application_close"], where=where),
            status=row["status"],
        )
    return policies


def _aligned_documents(row: dict[str, str], where: str) -> tuple[str, ...]:
    """``source_document`` 按 ``|`` 与 ``source_span`` 逐段对齐。

    整列留空表示每一段都出自政策本体；写了就必须段数相等——段数对不上时宁可报错，
    也不能猜哪一段属于哪份文件，否则校验会拿错正文比对，结论看起来还正常。
    """

    raw = (row.get("source_document") or "").strip()
    if not raw:
        return ()
    parts = tuple(part.strip() for part in raw.split("|"))
    count = len(tuple(part.strip() for part in row["source_span"].split("|") if part.strip()))
    if len(parts) != count:
        raise DatasetError(
            f"{where} 的 source_document 有 {len(parts)} 段，source_span 有 {count} 段，"
            "无法逐段对齐"
        )
    return parts


def load_criteria(
    data_dir: Path, policies: Mapping[tuple[str, str], Policy]
) -> dict[tuple[str, str], Criterion]:
    rows = _read_rows(data_dir / "policies" / "criteria_review.csv", CRITERIA_COLUMNS)
    codes = {code for code, _ in policies}
    criteria: dict[tuple[str, str], Criterion] = {}
    for row in rows:
        if row["review_status"] != "已仲裁":
            continue  # 未仲裁的条件不进对照：它不是共识答案
        where = f"criteria_review.csv 的 {row['policy_id']} {row['criterion_id']}"
        key = (row["policy_id"], row["criterion_id"])
        if key in criteria:
            raise DatasetError(f"{where} 重复")
        if row["policy_id"] not in codes:
            raise DatasetError(f"{where} 的政策代号不在 source_register.csv 里")
        try:
            expression = json.loads(row["adjudicated_expression"] or "{}")
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{where} 的 adjudicated_expression 不是合法 JSON") from exc
        if not isinstance(expression, dict) or not expression:
            raise DatasetError(f"{where} 没有可用的仲裁表达式")
        criteria[key] = Criterion(
            code=row["criterion_id"],
            policy_code=row["policy_id"],
            source_span=row["source_span"],
            section=row["source_page_or_section"],
            expression=expression,
            documents=_aligned_documents(row, where),
            version_document=row["source_version"].strip(),
        )
    if not criteria:
        raise DatasetError("criteria_review.csv 里没有已仲裁的条件")
    return criteria


def load_enterprises(data_dir: Path) -> dict[str, Enterprise]:
    rows = _read_rows(data_dir / "fixtures" / "enterprises.csv", ENTERPRISE_COLUMNS)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["enterprise_id"], []).append(row)

    enterprises: dict[str, Enterprise] = {}
    for code, items in grouped.items():
        names = {item["name_synthetic"] for item in items}
        regions = {item["region"] for item in items}
        industries = {item["industry"] for item in items}
        if len(names) > 1 or len(regions) > 1 or len(industries) > 1:
            raise DatasetError(f"企业 {code} 的同一列有多份取值，无法确定用哪一份")
        facts: list[Fact] = []
        # 同一键可以有多段期间，甚至起点相同、终点不同（``rdAddDeductionClaimed``
        # 就同时登记了 2024 全年与 2024—2025 两段），所以按 (键, 起, 止) 判重，
        # 与库里 ``enterprise_fact`` 的主键一致。
        seen: set[tuple[str, date, date]] = set()
        for item in items:
            where = f"enterprises.csv 的 {code} {item['fact_key']}"
            if item["fact_key"] not in FACT_UNITS:
                raise DatasetError(f"{where} 不是事实字典里的键")
            if item["unit"] != FACT_UNITS[item["fact_key"]]:
                raise DatasetError(
                    f"{where} 的单位是 {item['unit']}，字典要求 {FACT_UNITS[item['fact_key']]}"
                )
            start, end = _split_period(item["period"], where=where)
            key = (item["fact_key"], start, end)
            if key in seen:
                raise DatasetError(f"{where} 在 {start}/{end} 这段期间登记了两次")
            seen.add(key)
            value = item["fact_value"] or None
            if value is not None and FACT_UNITS[item["fact_key"]] in {"CNY", "person", "ratio"}:
                try:
                    Decimal(value)
                except InvalidOperation as exc:
                    raise DatasetError(f"{where} 的值「{value}」不是数") from exc
            facts.append(
                Fact(
                    key=item["fact_key"],
                    value=value,
                    unit=item["unit"],
                    period_start=start,
                    period_end=end,
                    source=item["source"],
                    verified=item["verified"].lower() == "true",
                )
            )
        enterprises[code] = Enterprise(
            code=code,
            name=items[0]["name_synthetic"],
            region=items[0]["region"],
            industry=items[0]["industry"],
            facts=tuple(sorted(facts, key=lambda fact: (fact.key, fact.period_start))),
        )
    return enterprises


def load_labels(
    data_dir: Path,
    policies: Mapping[tuple[str, str], Policy],
    criteria: Mapping[tuple[str, str], Criterion],
    enterprises: Mapping[str, Enterprise],
) -> list[Label]:
    rows = _read_rows(data_dir / "evaluation" / "labels.csv", LABEL_COLUMNS)
    labels: list[Label] = []
    for row in rows:
        where = f"labels.csv 的 {row['case_id']} {row['criterion_id']}"
        if row["adjudicated_result"] not in LABEL_RESULTS:
            raise DatasetError(f"{where} 的仲裁结果是「{row['adjudicated_result']}」，不在三值里")
        if (row["policy_id"], row["policy_version"]) not in policies:
            raise DatasetError(f"{where} 的政策版本不在 source_register.csv 里")
        if (row["policy_id"], row["criterion_id"]) not in criteria:
            raise DatasetError(f"{where} 在 criteria_review.csv 里没有已仲裁的条件")
        if row["enterprise_id"] not in enterprises:
            raise DatasetError(f"{where} 的企业不在 enterprises.csv 里")
        labels.append(
            Label(
                case_id=row["case_id"],
                enterprise=row["enterprise_id"],
                policy_code=row["policy_id"],
                document=row["policy_version"],
                criterion_code=row["criterion_id"],
                result=row["adjudicated_result"],
                split=row["split"],
            )
        )
    keys = [label.key for label in labels]
    if len(set(keys)) != len(keys):
        raise DatasetError("labels.csv 有重复的 (case_id, criterion_id)")
    return labels


def load_dataset(data_dir: Path | None = None) -> Dataset:
    directory = Path(data_dir) if data_dir is not None else REPO_ROOT / "data"
    policies = load_policies(directory)
    criteria = load_criteria(directory, policies)
    enterprises = load_enterprises(directory)
    labels = load_labels(directory, policies, criteria, enterprises)

    by_case: dict[str, list[Label]] = {}
    for label in labels:
        by_case.setdefault(label.case_id, []).append(label)

    cases: list[Case] = []
    excluded: list[Case] = []
    for case_id in sorted(by_case):
        group = by_case[case_id]
        first = group[0]
        if any(
            (label.enterprise, label.policy_code, label.document)
            != (first.enterprise, first.policy_code, first.document)
            for label in group
        ):
            raise DatasetError(f"{case_id} 的标签指向了不同的企业或政策版本")
        case = Case(
            case_id=case_id,
            enterprise=first.enterprise,
            policy_code=first.policy_code,
            document=first.document,
            labels=tuple(sorted(group, key=lambda label: label.criterion_code)),
        )
        if (case.policy_code, case.document) in EXCLUDED_VERSIONS:
            excluded.append(case)
        else:
            cases.append(case)

    return Dataset(
        data_dir=directory,
        policies=policies,
        criteria=criteria,
        enterprises=enterprises,
        cases=tuple(cases),
        excluded=tuple(excluded),
    )


def describe(dataset: Dataset) -> str:
    """一行摘要：case 数、分母、dev/holdout 与三类标注的条数。"""

    scored = dataset.scored_labels()
    dev = sum(1 for label in scored if label.split == "dev")
    holdout = sum(1 for label in scored if label.split == "holdout")
    counts = {name: sum(1 for label in scored if label.result == name) for name in LABEL_RESULTS}
    excluded = dataset.excluded_labels()
    return (
        f"{len(dataset.cases)} 个 case、{len(scored)} 条标签（dev {dev} / holdout {holdout}）；"
        f"satisfied {counts['satisfied']} / insufficient_evidence "
        f"{counts['insufficient_evidence']} / not_satisfied {counts['not_satisfied']}；"
        f"另排除 {len(excluded)} 条（{len(dataset.excluded)} 个 case，见 EXCLUDED_VERSIONS）"
    )


def as_prompt_facts(enterprise: Enterprise) -> Iterable[dict[str, Any]]:
    """把事实摊成给模型看的形式：键、值、单位、期间、来源、是否已核实。

    ``value`` 为 ``None`` 时表示「未知」，不能省掉这一行——省略与「未知」在判断上
    是同一件事，但对模型来说「没列」更容易被当成「不适用」。
    """

    for fact in enterprise.facts:
        yield {
            "key": fact.key,
            "value": fact.value,
            "unit": fact.unit,
            "period": f"{fact.period_start.isoformat()}/{fact.period_end.isoformat()}",
            "source": fact.source,
            "verified": fact.verified,
        }
