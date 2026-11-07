#!/usr/bin/env python3
"""把 ``data/`` 里已仲裁的评测数据集灌进服务库（``public`` schema）。

**为什么不用 `python -m app.ablation pipeline`**：那是消融对照工具，它灌的是临时
schema，而且把 ``validation_status`` 直接写成 ``passed``（条件被视为预设的共识
答案，不走引用校验）。这里要的是「库里真有这套数据，且每条引文都过产品自己的
校验」，所以走产品的写入口，与 admin 导入接口同一组函数：

    create_policy_version → validate（app.routes.admin_policies._collect_validation）
    → save_criteria → publish
    create_enterprise

**默认干跑**：只读 CSV 与正文，把每条引文按产品规则比对一遍，打印「哪个政策版本
能发布、哪条条件会挡住发布」，不连库、不写库。加 ``--apply`` 才写。

用法（ECS 上；venv 解释器）：

    PYTHONPATH=/opt/huice/app/services/api \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/scripts/import_eval_dataset.py
    # 确认输出无误后再：
    ... import_eval_dataset.py --apply

写入前按 ``document_number`` 读库里的状态再决定：已完整发布且内容一致的整版跳过；
中断留下的草稿（``pending_parse`` / ``pending_review``）从中断处续跑；状态或内容对不上
的一律非零退出，交人工核对。企业按合成名称判重。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.agents.ingestion import PreparedSource, validate_official_url  # noqa: E402
from app.errors import ApiProblem  # noqa: E402
from app.schemas import PolicyDraft, PolicyImportMetadata, SourceSpan  # noqa: E402
from app.routes.admin_policies import _collect_validation, _domain_criterion  # noqa: E402

DATA = ROOT / "data"
SOURCES = DATA / "policies" / "sources"
SHANGHAI = timezone(timedelta(hours=8))

#: 登记表没有 applicable_entities 列。它与缺省写法与 ablation 一致：只进政策详情
#: 与准备清单模板，不参与条件求值，因此不影响四态指标。
APPLICABLE_ENTITIES = ["企业"]

#: 引文分段的分隔符：``criteria_review.csv`` 的 source_span 用 ``|`` 连多段原文。
SPAN_SEPARATOR = "|"


# ------------------------------------------------------------------ 读数据


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _optional_date(text: str) -> date | None:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def _source_text(name: str) -> str | None:
    path = SOURCES / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _norm(text: str) -> str:
    """与 ``app.domain.citations._normalize`` 同一口径：只容忍空白差异。"""

    return "".join(text.split())


# ------------------------------------------------------------------ 计划结构


@dataclass(frozen=True, slots=True)
class SpanPlan:
    source_name: str
    quote: str
    page: int | None
    section: str | None


@dataclass(slots=True)
class CriterionPlan:
    code: str
    expression: dict[str, Any]
    review_finding: str
    spans: list[SpanPlan] = field(default_factory=list)
    problem: str | None = None


@dataclass(slots=True)
class PolicyPlan:
    code: str
    document: str
    metadata: PolicyImportMetadata
    body_name: str
    body_text: str
    attachments: list[tuple[str, str]] = field(default_factory=list)
    criteria: list[CriterionPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VersionState:
    """库里某个文号当前的样子；重跑靠它区分「已完成 / 可续跑 / 对不上」。

    只取判断需要的字段，不拉正文：正文可能有上百 KB，而这里只比哈希。
    """

    policy_id: UUID
    version: str
    review_status: str
    revision: UUID
    #: ``(条件代号, validation_status)``，按代号排序。
    criteria: tuple[tuple[str, str], ...]
    #: 该版本已落库的全部来源内容哈希。
    source_hashes: frozenset[str]


# ------------------------------------------------------------------ 组装计划


def _split_spans(raw_span: str) -> list[str]:
    return [part.strip() for part in (raw_span or "").split(SPAN_SEPARATOR) if part.strip()]


def _location(text: str) -> tuple[int | None, str | None]:
    """``source_page_or_section`` 形如「第五条」或「12」；纯数字当页码。"""

    text = (text or "").strip()
    if not text:
        return None, None
    return (int(text), None) if text.isdigit() else (None, text)


def _metadata(row: dict[str, str]) -> tuple[PolicyImportMetadata, list[str]]:
    notes: list[str] = []
    effective_from = date.fromisoformat(row["effective_from"])
    published_at = date.fromisoformat(row["published_at"])
    # 产品要求「生效日期不早于发布日期」，而登记表里有先执行后发文的版本
    # （研发加计 2023-08 发文、2023-01-01 起执行）。为满足约束把发布日压到生效日，
    # 与 ablation 的做法一致；这是与真实政策有出入的地方，必须显式报出来。
    if published_at > effective_from:
        notes.append(
            f"发布日 {published_at} 晚于生效日 {effective_from}，按产品约束压到生效日"
        )
        published_at = effective_from
    window = None
    opens, closes = _optional_date(row.get("application_open", "")), _optional_date(
        row.get("application_close", "")
    )
    if opens is not None and closes is not None:
        window = {
            "opensAt": datetime.combine(opens, time(0, 0), SHANGHAI).isoformat(),
            "closesAt": datetime.combine(closes, time(23, 59, 59), SHANGHAI).isoformat(),
        }
    metadata = PolicyImportMetadata.model_validate(
        {
            "version": row["version"],
            "title": row["title"],
            "type": row["type"],
            "issuer": row["issuer"],
            "region": row["region"],
            "document_number": row["version"],
            "applicable_entities": APPLICABLE_ENTITIES,
            "published_at": published_at,
            "effective_from": effective_from,
            "effective_to": _optional_date(row.get("effective_to", "")),
            "application_window": window,
            "source_url": row["source_url"],
            "official_entry_url": row["source_url"],
        }
    )
    return metadata, notes


def build_plans() -> tuple[list[PolicyPlan], list[str]]:
    register = _rows(DATA / "policies" / "source_register.csv")
    criteria_rows = _rows(DATA / "policies" / "criteria_review.csv")
    labels = _rows(DATA / "evaluation" / "labels.csv")

    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in register:
        by_code[row["policy_id"]].append(row)

    crit_by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in criteria_rows:
        if (row.get("review_status") or "").strip() != "已仲裁":
            continue
        crit_by_code[row["policy_id"]].append(row)

    # 要灌的版本：有已仲裁条件的政策 + labels 引用的文号
    wanted: list[tuple[str, str]] = []
    for row in register:
        code = row["policy_id"]
        if not crit_by_code.get(code):
            continue
        wanted.append((code, row["version"]))
    known = set(wanted)
    for row in labels:
        key = (row["policy_id"], row["policy_version"])
        if key not in known and key in {(r["policy_id"], r["version"]) for r in register}:
            wanted.append(key)
            known.add(key)

    reg_index = {(row["policy_id"], row["version"]): row for row in register}
    plans: list[PolicyPlan] = []
    problems: list[str] = []

    for code, document in sorted(set(wanted)):
        row = reg_index[(code, document)]
        metadata, notes = _metadata(row)
        try:
            validate_official_url(metadata.source_url, "sourceUrl")
            validate_official_url(metadata.official_entry_url, "officialEntryUrl")
        except Exception as exc:  # ApiProblem
            problems.append(f"{code} @ {document}：{exc}")
            continue

        body_text = _source_text(document) or _source_text(code)
        if body_text is None:
            problems.append(f"{code} @ {document}：sources/ 下没有正文（既非文号也非政策代号命名）")
            continue
        body_name = document if _source_text(document) is not None else code

        plan = PolicyPlan(
            code=code,
            document=document,
            metadata=metadata,
            body_name=body_name,
            body_text=body_text,
            notes=notes,
        )
        versions = {r["version"] for r in by_code[code]}
        sources = [(body_name, body_text)]

        for crit in crit_by_code.get(code, []):
            # 一行有两列在说到处：``source_version`` 是政策版本（对齐用），
            # ``source_document`` 是这句话实际引自哪份文件（可能另属一份法规或申报通知）。
            # 后者是空的就退回前者。
            version_cited = (crit.get("source_version") or "").strip()
            if version_cited and version_cited != document and version_cited in versions:
                continue  # 属于同一政策的另一个版本，归那一版处理
            cited_docs = [
                name
                for name in _split_spans(crit.get("source_document") or "") + [version_cited]
                if name and name != document
            ]
            for cited in dict.fromkeys(cited_docs):
                extra = _source_text(cited)
                if extra is None:
                    # 被引文件没单独登记正文：它必须是本版正文的一部分（如申报通知）
                    plan.notes.append(
                        f"{crit['criterion_id']} 的出处 {cited} 无独立文件，按本版正文校验"
                    )
                elif all(name != cited for name, _ in sources):
                    sources.append((cited, extra))
                    plan.attachments.append((cited, extra))
            plan.criteria.append(_criterion_plan(crit, sources))

        plans.append(plan)

    return plans, problems


def _criterion_plan(crit: dict[str, str], sources: list[tuple[str, str]]) -> CriterionPlan:
    code = crit["criterion_id"]
    try:
        expression = json.loads(crit["adjudicated_expression"])
    except json.JSONDecodeError:
        return CriterionPlan(code=code, expression={}, review_finding="none", problem="仲裁表达式不是合法 JSON")
    if not isinstance(expression, dict) or not expression:
        return CriterionPlan(code=code, expression={}, review_finding="none", problem="仲裁表达式为空")

    page, section = _location(crit.get("source_page_or_section", ""))
    finding = (crit.get("review_finding") or "").strip() or "none"
    plan = CriterionPlan(code=code, expression=expression, review_finding=finding)

    for quote in _split_spans(crit.get("source_span", "")):
        wanted = _norm(quote)
        chosen = next((name for name, text in sources if wanted in _norm(text)), None)
        if chosen is None:
            plan.problem = f"引文在可用正文里找不到：「{quote[:40]}」"
            break
        plan.spans.append(SpanPlan(source_name=chosen, quote=quote, page=page, section=section))
    if not plan.spans and plan.problem is None:
        plan.problem = "没有可用的引文"
    return plan


# ------------------------------------------------------------------ 干跑校验


def _source_id(document: str, name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"huice-eval/{document}/{name}")


def _sources_of(plan: PolicyPlan) -> list[tuple[str, str, str]]:
    body = (plan.body_name, plan.body_text, "body")
    return [body] + [(name, text, "attachment") for name, text in plan.attachments]


def _draft_for(plan: PolicyPlan) -> PolicyDraft:
    meta = plan.metadata
    docs = [
        {
            "id": _source_id(plan.document, name),
            "kind": kind,
            "source_url": meta.source_url,
            "title": name,
            "fetched_at": datetime.now(timezone.utc),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
        }
        for name, text, kind in _sources_of(plan)
    ]
    return PolicyDraft.model_validate(
        {
            "id": uuid5(NAMESPACE_URL, f"huice-eval/policy/{plan.document}"),
            "version": meta.version,
            "title": meta.title,
            "type": meta.policy_type,
            "issuer": meta.issuer,
            "region": meta.region,
            "document_number": meta.document_number,
            "applicable_entities": meta.applicable_entities,
            "published_at": meta.published_at,
            "effective_from": meta.effective_from,
            "effective_to": meta.effective_to,
            "application_window": meta.application_window,
            "source_url": meta.source_url,
            "official_entry_url": meta.official_entry_url,
            "status": "pending_review",
            "revision": uuid4(),
            "sources": docs,
            "criteria": [],
            "validation_findings": [],
        }
    )


def _spans_for(plan: PolicyPlan, crit: CriterionPlan) -> list[SourceSpan]:
    known = {name for name, _, _ in _sources_of(plan)}
    spans = []
    for span in crit.spans:
        if span.source_name not in known:
            raise SystemExit(f"{crit.code} 的引文出处 {span.source_name} 不在本版来源里")
        spans.append(
            SourceSpan.model_validate(
                {
                    "source_id": _source_id(plan.document, span.source_name),
                    "source_url": plan.metadata.source_url,
                    "source_version": plan.metadata.version,
                    "page": span.page,
                    "section": span.section,
                    "quote": span.quote,
                    "text_hash": hashlib.sha256(span.quote.encode("utf-8")).hexdigest(),
                }
            )
        )
    return spans


def _validate(plan: PolicyPlan, draft: PolicyDraft) -> tuple[dict[str, str], dict[str, list[str]]]:
    """用产品自己的校验函数算一遍：返回 {条件代号: 状态} 与 {条件代号: finding 码}。"""

    criteria = []
    issues: dict[str, list[str]] = defaultdict(list)
    for crit in plan.criteria:
        if crit.problem is not None:
            issues[crit.code].append(crit.problem)
            continue
        cid = uuid5(NAMESPACE_URL, f"huice-eval/criterion/{plan.document}/{crit.code}")
        criteria.append(_domain_criterion(cid, crit.code, crit.expression, _spans_for(plan, crit), crit.review_finding))

    status: dict[str, str] = {crit.code: ("blocked" if crit.problem else "pending") for crit in plan.criteria}
    if criteria:
        records, findings = _collect_validation(draft, criteria)
        by_id = {str(record.id): record for record in records}
        for crit in plan.criteria:
            cid = str(uuid5(NAMESPACE_URL, f"huice-eval/criterion/{plan.document}/{crit.code}"))
            record = by_id.get(cid)
            if record is not None:
                status[crit.code] = record.validation_status
        for finding in findings:
            owner = next(
                (crit.code for crit in plan.criteria
                 if str(uuid5(NAMESPACE_URL, f"huice-eval/criterion/{plan.document}/{crit.code}")) == str(finding.criterion_id)),
                "(政策级)",
            )
            issues[owner].append(finding.code)
    problems = {code: sorted(set(codes)) for code, codes in issues.items()}
    return status, problems


# ------------------------------------------------------------------ 写库


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prepared(plan: PolicyPlan) -> list[PreparedSource]:
    return [
        PreparedSource(
            kind=kind,
            file_name=f"{name}.txt",
            mime_type="text/plain",
            text=text,
            content_hash=_digest(text),
            byte_size=len(text.encode("utf-8")),
        )
        for name, text, kind in _sources_of(plan)
    ]


def _storage_keys(plan: PolicyPlan) -> list[str]:
    return [f"evaluation/{plan.document}/{name}.txt" for name, _, _ in _sources_of(plan)]


def _version_state(dsn: str, document_number: str) -> list[VersionState]:
    """读库：这个文号现在有几个版本、各自停在哪一步。"""

    import psycopg
    from psycopg.rows import dict_row

    states: list[VersionState] = []
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, policy_id, version, review_status, revision FROM policy_version "
            "WHERE document_number = %s ORDER BY imported_at, id",
            (document_number,),
        )
        for row in cur.fetchall():
            cur.execute(
                "SELECT title, validation_status FROM policy_criterion "
                "WHERE policy_version_id = %s ORDER BY title",
                (row["id"],),
            )
            criteria = tuple((item["title"], item["validation_status"]) for item in cur.fetchall())
            cur.execute(
                "SELECT content_hash FROM policy_source WHERE policy_version_id = %s",
                (row["id"],),
            )
            states.append(
                VersionState(
                    policy_id=row["policy_id"],
                    version=row["version"],
                    review_status=row["review_status"] or "",
                    revision=row["revision"],
                    criteria=criteria,
                    source_hashes=frozenset(item["content_hash"] for item in cur.fetchall()),
                )
            )
    return states


def _decide(
    states: list[VersionState], plan: PolicyPlan, source_hashes: frozenset[str]
) -> tuple[str, str]:
    """重跑该干什么：``create`` 新建、``resume`` 续跑、``skip`` 跳过、``conflict`` 交人工。

    返回 ``(动作, 理由)``。判断只看库里已有的记录，不猜：状态不认识、条件缺了、
    校验不是 passed、来源变过，都算 ``conflict``——宁可让运维看一眼，也不能让
    「进程在中途被杀」看起来像「已经导好了」。
    """

    if not states:
        return "create", "库里没有这个文号"
    if len(states) > 1:
        return (
            "conflict",
            f"库里有 {len(states)} 个版本共用文号 {plan.document}，无法判断该续跑哪一个",
        )

    state = states[0]
    expected = {crit.code for crit in plan.criteria}
    missing = sorted(expected - {code for code, _ in state.criteria})
    blocked = sorted(code for code, value in state.criteria if value != "passed")

    if state.review_status == "published":
        if missing:
            return "conflict", f"已发布，但库里缺条件 {'、'.join(missing)}"
        if blocked:
            return "conflict", f"已发布，但有条件不是 passed：{'、'.join(blocked)}"
        if state.source_hashes != source_hashes:
            return "conflict", "已发布，但来源与本轮计划不一致（正文或附件变过）"
        return "skip", f"已发布，{len(state.criteria)} 条条件全部 passed"

    if state.review_status in {"pending_parse", "pending_review"}:
        if state.review_status == "pending_parse" and state.criteria:
            return "conflict", "状态是 pending_parse 却已有条件记录，库被改过"
        if state.source_hashes != source_hashes:
            return "conflict", "草稿的来源与本轮计划不一致（正文或附件变过）"
        return "resume", f"未发布（{state.review_status}），从中断处续跑"

    return "conflict", f"未识别的状态 {state.review_status!r}"


def _apply(plans: list[PolicyPlan], dsn: str, *, repository: Any | None = None) -> int:
    """逐版写库：先读状态，再决定新建、续跑还是拒绝。

    写库分三步，每步各自一个事务，因此中断点只有三个，也正好是库里能看到的三种状态：

    1. ``create_policy_version``：版本 + 来源 + 轨迹同一个事务。中断 → 版本在、
       没有条件（``pending_parse``）。
    2. ``save_criteria``：整版替换条件与校验发现（按 revision 做 CAS）。中断 →
       条件在、未发布（``pending_review``）。
    3. ``publish``：翻状态并写失效事件。中断 → 已发布（``published``）。

    重跑据此分流：已完整发布的跳过；前两种续跑（``save_criteria`` 是整版替换，重写
    不会叠加）；状态或内容对不上的一律非零退出。**不会出现「跳过即成功」**——
    这正是 M2 在 #36 复审里指出、旧写法按文号 ``SELECT 1`` 就跳过的问题。

    企业画像最后统一写，按合成名称判重。
    """

    if repository is None:
        from app.db import Database
        from app.integrations.storage import PolicyRepository

        repository = PolicyRepository(Database(dsn), cursor_secret="eval-import")
    failed = 0

    for plan in plans:
        meta = plan.metadata
        digests = {name: _digest(text) for name, text, _ in _sources_of(plan)}
        states = _version_state(dsn, meta.document_number)
        action, reason = _decide(states, plan, frozenset(digests.values()))

        if action == "skip":
            print(f"跳过 {plan.document}：{reason}")
            continue
        if action == "conflict":
            print(f"拒绝 {plan.document}：{reason}。请人工核对后再跑 --apply。")
            failed += 1
            continue
        if any(crit.problem for crit in plan.criteria):
            for crit in plan.criteria:
                if crit.problem:
                    print(f"跳过 {plan.document}：{crit.code} {crit.problem}")
            failed += 1
            continue

        try:
            if action == "create":
                draft = repository.create_policy_version(
                    metadata=meta,
                    sources=_prepared(plan),
                    storage_keys=_storage_keys(plan),
                    trace_id=str(uuid4()),
                )
                print(f"已建草稿 {plan.document}（{len(draft.sources)} 个来源）")
            else:
                draft = repository.get_version_for_edit(states[0].policy_id, states[0].version)
                print(f"续跑 {plan.document}：{reason}")
        except ApiProblem as exc:
            print(f"拒绝 {plan.document}：{exc.code} {exc.message}")
            failed += 1
            continue

        ids = {source.content_hash: source.id for source in draft.sources}
        absent = [name for name, digest in digests.items() if digest not in ids]
        if absent:
            print(f"拒绝 {plan.document}：库里缺来源 {'、'.join(absent)}，请人工核对。")
            failed += 1
            continue

        criteria = []
        for crit in plan.criteria:
            spans = []
            for span in crit.spans:
                digest = digests[span.source_name]
                spans.append(
                    SourceSpan.model_validate(
                        {
                            "source_id": ids[digest],
                            "source_url": meta.source_url,
                            "source_version": meta.version,
                            "page": span.page,
                            "section": span.section,
                            "quote": span.quote,
                            "text_hash": _digest(span.quote),
                        }
                    )
                )
            criteria.append(
                _domain_criterion(
                    uuid4(),
                    crit.code,
                    crit.expression,
                    spans,
                    crit.review_finding,
                )
            )
        records, findings = _collect_validation(draft, criteria)
        saved = repository.save_criteria(
            policy_id=draft.id,
            version=draft.version,
            expected_revision=draft.revision,
            criteria=records,
            findings=findings,
            status="pending_review",
            trace_id=str(uuid4()),
        )
        blocked = [record.title for record in records if record.validation_status != "passed"]
        if blocked:
            print(f"未发布 {plan.document}：{len(blocked)} 条条件校验未通过（{'、'.join(blocked)}）")
            failed += 1
            continue
        repository.publish(
            policy_id=draft.id,
            version=draft.version,
            expected_revision=saved.revision,
            trace_id=str(uuid4()),
        )
        print(f"已发布 {plan.document}：{len(records)} 条条件")

    _apply_enterprises(repository, dsn)
    return failed


def _fact_value(fact: dict[str, str], where: str, problems: list[str] | None) -> Any:
    """把 CSV 里的值转成契约要的类型；布尔列的空值是「未知」，不是 false。

    只认 ``true``/``false``（大小写不敏感）。空串以前会被 ``== "true"`` 悄悄转成
    ``False``，等于把「没填」记成「不符合」，M2 在 #36 复审指出过；其余非法写法也
    一律报出来，不猜。
    """

    if fact["unit"] != "boolean":
        return fact["fact_value"]
    raw = fact["fact_value"].strip().lower()
    if not raw:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    message = f"{where}：布尔事实只能写 true/false 或留空，实际是 {fact['fact_value']!r}"
    if problems is None:
        raise ValueError(message)
    problems.append(message)
    return None


def _enterprise_payloads(
    problems: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """labels 用到的企业 → 契约的 ``EnterpriseInput`` 形状（与 ablation 的写法一致）。

    ``problems`` 传列表时把无法转换的事实记进去（由调用方汇总成预检失败）；
    不传就直接抛 ``ValueError``——写库路径不该带着未报告的问题继续。
    """

    labels = _rows(DATA / "evaluation" / "labels.csv")
    wanted = {row["enterprise_id"] for row in labels}
    rows = [
        row
        for row in _rows(DATA / "fixtures" / "enterprises.csv")
        if row["enterprise_id"] in wanted
    ]

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["enterprise_id"]].append(row)

    payloads: list[tuple[str, dict[str, Any]]] = []
    for code, facts in sorted(grouped.items()):
        start, end = facts[0]["period"].split("/")
        payloads.append(
            (
                code,
                {
                    "nameSynthetic": facts[0]["name_synthetic"].strip(),
                    "period": {"startDate": start.strip(), "endDate": end.strip()},
                    "facts": [
                        {
                            "key": fact["fact_key"],
                            # CSV 里的布尔写成 true/false，契约要真布尔；空值是「未知」，
                            # 保持 null——转成 false 会把「没填」记成「不符合」。
                            "value": _fact_value(fact, f"{code}/{fact['fact_key']}", problems),
                            "unit": fact["unit"],
                            "period": {
                                "startDate": fact["period"].split("/")[0].strip(),
                                "endDate": fact["period"].split("/")[1].strip(),
                            },
                            "source": fact["source"],
                        }
                        for fact in facts
                    ],
                },
            )
        )
    return payloads


def check_enterprises() -> list[str]:
    """干跑：企业画像按产品的规范化规则过一遍，不连库。"""

    from app.agents.profile import normalize_profile
    from app.schemas import EnterpriseInput

    problems: list[str] = []
    for code, payload in _enterprise_payloads(problems):
        try:
            normalize_profile(EnterpriseInput.model_validate(payload))
        except Exception as exc:  # ValidationError / ApiProblem
            problems.append(f"{code}：{exc}")
            continue
        print(f"  企业 {code}：{payload['nameSynthetic']}，事实 {len(payload['facts'])} 条")
    return problems


def _apply_enterprises(repository: Any, dsn: str) -> None:
    import psycopg

    from app.agents.profile import normalize_profile
    from app.schemas import EnterpriseInput

    problems: list[str] = []
    payloads = _enterprise_payloads(problems)
    if problems:  # main 已在预检拦下；能走到这里说明调用方绕过了校验
        raise ValueError("企业事实预检未通过，不应写库：" + "；".join(problems))

    for code, payload in payloads:
        name = payload["nameSynthetic"]
        with psycopg.connect(dsn) as conn:
            exists = conn.execute(
                "SELECT 1 FROM enterprise WHERE name_synthetic = %s", (name,)
            ).fetchone()
        if exists:
            print(f"跳过企业 {code}：同名企业已在库中")
            continue
        created = repository.create_enterprise(
            profile=normalize_profile(EnterpriseInput.model_validate(payload)),
            trace_id=str(uuid4()),
        )
        print(f"已创建企业 {code}：{created.name_synthetic}（{len(payload['facts'])} 条事实）")


# ------------------------------------------------------------------ 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_eval_dataset.py", description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正写库；默认只干跑")
    parser.add_argument("--dsn", help="库连接串；默认读 DATABASE_URL")
    args = parser.parse_args(argv)

    plans, problems = build_plans()
    for problem in problems:
        print(f"组装失败：{problem}")

    blocking = 0
    for plan in plans:
        draft = _draft_for(plan)
        status, issues = _validate(plan, draft)
        publishable = all(value == "passed" for value in status.values())
        print()
        print(f"{plan.document}  （{plan.code}）  {'可发布' if publishable else '有拦截'}")
        print(f"  正文 {plan.body_name}.txt" + (f"，附带 {len(plan.attachments)} 份出处" if plan.attachments else ""))
        for note in plan.notes:
            print(f"  说明：{note}")
        for crit in plan.criteria:
            codes = "、".join(issues.get(crit.code, []))
            print(f"  {crit.code:<12} {status[crit.code]:<8} {codes}")
        if not publishable:
            blocking += 1

    print()
    print("企业画像（按产品规范化规则校验）：")
    enterprise_problems = check_enterprises()
    for problem in enterprise_problems:
        print(f"  组装失败：{problem}")

    print()
    print(
        f"政策版本 {len(plans)} 个，可发布 {len(plans) - blocking} 个，有拦截 {blocking} 个；"
        f"组装失败 {len(problems) + len(enterprise_problems)} 条"
    )
    problems = problems + enterprise_problems

    # 预检不过就一律不写：干跑和 --apply 走同一道门，别让 --apply 绕过校验把
    # 其余计划发出去。M2 在 #36 复审指出过这一点。
    if blocking or problems:
        print("预检未通过：不写库。修好上面的问题再跑 --apply。")
        return 1

    if not args.apply:
        print("干跑结束：没有连接数据库。加 --apply 才写。")
        return 0

    dsn = (args.dsn or os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("缺少库地址：设 DATABASE_URL 或用 --dsn 传。", file=sys.stderr)
        return 2
    failed = _apply(plans, dsn)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
