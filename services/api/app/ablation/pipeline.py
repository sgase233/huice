"""现方案跑同一批输入：临时库灌政策与企业，逐 case 走真实的 ``assess()``。

为什么中间隔一层数据库而不是直接调 ``domain/rules.py``：要比较的是**这条链路**，
不是规则函数的纯计算。灌进去的是已仲裁条件（``adjudicated_expression``），
两个方案拿到的是同一套条件——条件抽取的差异由 ``extraction`` 那条单独量，
不然基线拿条件清单、现方案拿抽样规则，比的就不是同一件事了。

四处口径写在这里，避免下次重新推：

* **发布日压到生效日**。``policy_version_dates`` 要求 ``effective_from >=
  published_at``，而登记表里有三个版本是先执行后发文（``CN-TAX-SME-2023-012``
  2023-08-02 发文、2023-01-01 起执行；``CN-TAX-GXJS`` 两个版本同理）。灌库时把
  发布日取 ``min(发文日, 生效日)`` **只为满足约束**，报告里要说明这与真实政策
  有出入——改的是临时库，不是 CSV。
* **申报窗口用登记表的真实起止**，不拿生效期顶替：只有 SMART / SRDI / GREEN
  三项有 ``application_open/close``，其余留空。
* **``applicable_entities`` 登记表没有这一列**，统一填「企业」占位；它只进政策
  详情与准备清单模板，不参与条件求值，因此不影响四态指标。
* **不传缓存**（``assess(cache=None)`` → 进程内 ``DISABLED_CACHE``）：每个 case
  都真算一遍，既不读也不写 ``huice:eval:v1:*``，否则第二轮会命中上一轮的求值，
  耗时与轨迹都不是这一轮的。

只灌**进分子分母的 case** 用得到的版本与企业。被排除的 2008 版（C031/C033）
不灌：它与 2016 版共用同一个政策容器，两个版本都在库里会让「取最新已发布版本」
变成按时间戳抢答，且这两个 case 本来就不进指标。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..agents.contracts import AgentContext
from ..agents.ingestion import PreparedSource
from ..agents.orchestrator import assess
from ..agents.profile import normalize_profile
from ..db import Database
from ..domain.rules import RULE_VERSION
from ..errors import ApiProblem
from ..domain import versioning
from ..integrations.llm import LlmClient
from ..integrations.storage import CriterionRecord, PolicyRepository
from ..schemas import AssessmentInput, EnterpriseInput, PolicyImportMetadata
from .dataset import REPO_ROOT, Case, Dataset, Policy

#: 判断与申报窗口都按北京时间解释；契约里的时间都是带偏移的。
SHANGHAI = timezone(timedelta(hours=8))

#: 判断期间：企业事实就是按 2026 年这一年与四个季度登记的。
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 12, 31)

#: 登记表里没有「适用对象」这一列；填一个常量，见模块文档。
APPLICABLE_ENTITIES = ("企业",)


@dataclass(frozen=True, slots=True)
class SeededPolicy:
    policy_id: UUID
    version: str
    criteria: dict[str, UUID]

    def code_for(self, criterion_id: UUID) -> str | None:
        for code, value in self.criteria.items():
            if value == criterion_id:
                return code
        return None


@dataclass(slots=True)
class Seed:
    policies: dict[tuple[str, str], SeededPolicy] = field(default_factory=dict)
    enterprises: dict[str, UUID] = field(default_factory=dict)


def default_as_of() -> datetime:
    """运行当天的北京时间正午。两个方案用同一个值，报告里写这个日期。"""

    today = datetime.now(SHANGHAI).date()
    return datetime.combine(today, time(12, 0), SHANGHAI)


def _metadata(policy: Policy, *, policy_id: UUID | None) -> PolicyImportMetadata:
    payload: dict[str, Any] = {
        "version": policy.document,
        "title": policy.title,
        "type": policy.policy_type,
        "issuer": policy.issuer,
        "region": policy.region,
        "documentNumber": policy.document,
        "applicableEntities": list(APPLICABLE_ENTITIES),
        "publishedAt": min(policy.published_at, policy.effective_from).isoformat(),
        "effectiveFrom": policy.effective_from.isoformat(),
        "effectiveTo": policy.effective_to.isoformat() if policy.effective_to else None,
        "applicationWindow": None,
        "sourceUrl": policy.source_url,
        "officialEntryUrl": policy.source_url,
    }
    if policy_id is not None:
        payload["policyId"] = str(policy_id)
    if policy.application_open is not None and policy.application_close is not None:
        payload["applicationWindow"] = {
            "opensAt": datetime.combine(policy.application_open, time(0, 0), SHANGHAI).isoformat(),
            "closesAt": datetime.combine(
                policy.application_close, time(23, 59, 59), SHANGHAI
            ).isoformat(),
        }
    return PolicyImportMetadata.model_validate(payload)


def _source(policy: Policy, text: str) -> PreparedSource:
    raw = text.encode("utf-8")
    return PreparedSource(
        kind="body",
        file_name=f"{policy.document}.txt",
        mime_type="text/plain",
        text=text,
        content_hash=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
    )


def seed(dataset: Dataset, repository: PolicyRepository) -> Seed:
    """把用得到的政策版本与企业灌进库，返回代号 → UUID 的对照。"""

    seed_result = Seed()
    used = {(case.policy_code, case.document) for case in dataset.cases}
    documents_by_code: dict[str, set[str]] = {}
    for code, document in used:
        documents_by_code.setdefault(code, set()).add(document)
    shared = {code: docs for code, docs in documents_by_code.items() if len(docs) > 1}
    if shared:
        # 同一政策容器里灌两个版本时，「最新已发布版本」由发布时刻与 UUID 决定，
        # 不是由 asOf 决定；真要用就得先想清楚怎么固定版本，这里直接拒绝。
        detail = "、".join(f"{code}（{len(docs)} 个版本）" for code, docs in sorted(shared.items()))
        raise RuntimeError(f"进指标的 case 横跨同一政策的多个版本：{detail}")

    for code, document in sorted(used):
        policy = dataset.policies[(code, document)]
        body = _source(policy, dataset.source_text(policy))
        draft = repository.create_policy_version(
            metadata=_metadata(policy, policy_id=None),
            sources=[body],
            storage_keys=[f"evaluation/{policy.document}.txt"],
            trace_id=str(uuid4()),
        )

        body_source = draft.sources[0]
        records: list[CriterionRecord] = []
        mapping: dict[str, UUID] = {}
        # 同一政策的不同版本各有自己的条件（``GXJS-V1-001`` / ``GXJS-V2-001``）。
        # 只挂本版本的条件，外加出处是**另一份文件**（不是本政策的其它版本）的条件：
        # 研发加计、专精特新的条件引自上位文件或申报通知，属于本政策。
        other_versions = {doc for (other, doc) in dataset.policies if other == code}
        own = sorted(
            (
                item
                for (policy_code, _), item in dataset.criteria.items()
                if policy_code == code
                and (item.document == policy.document or item.document not in other_versions)
            ),
            key=lambda item: item.code,
        )
        wanted = {label.criterion_code for case in dataset.cases for label in case.labels
                  if (case.policy_code, case.document) == (code, document)}
        missing = wanted - {item.code for item in own}
        if missing:
            raise RuntimeError(
                f"{code} {document} 的标注里有条件没有仲裁表达式：{'、'.join(sorted(missing))}"
            )
        for criterion in own:
            criterion_id = uuid4()
            mapping[criterion.code] = criterion_id
            records.append(
                CriterionRecord(
                    id=criterion_id,
                    title=criterion.code,
                    expression=criterion.expression,
                    spans=[
                        {
                            "sourceId": str(body_source.id),
                            "sourceUrl": body_source.source_url,
                            "sourceVersion": policy.document,
                            "page": None,
                            "section": criterion.section,
                            "quote": span,
                            "textHash": body_source.content_hash,
                        }
                        for span in criterion.spans
                    ],
                    review_finding="none",
                    validation_status="passed",
                )
            )
        saved = repository.save_criteria(
            policy_id=draft.id,
            version=policy.document,
            expected_revision=draft.revision,
            criteria=records,
            findings=[],
            status="pending_review",
            trace_id=str(uuid4()),
        )
        repository.publish(
            policy_id=draft.id,
            version=policy.document,
            expected_revision=saved.revision,
            trace_id=str(uuid4()),
        )
        seed_result.policies[(code, document)] = SeededPolicy(
            policy_id=draft.id, version=policy.document, criteria=mapping
        )

    for code in sorted({case.enterprise for case in dataset.cases}):
        enterprise = dataset.enterprises[code]
        profile = normalize_profile(
            EnterpriseInput.model_validate(
                {
                    "nameSynthetic": enterprise.name,
                    "period": {
                        "startDate": PERIOD_START.isoformat(),
                        "endDate": PERIOD_END.isoformat(),
                    },
                    "facts": [
                        {
                            "key": fact.key,
                            "value": _wire_value(fact.value, fact.unit),
                            "unit": fact.unit,
                            "period": {
                                "startDate": fact.period_start.isoformat(),
                                "endDate": fact.period_end.isoformat(),
                            },
                            "source": fact.source,
                        }
                        for fact in enterprise.facts
                    ],
                }
            )
        )
        created = repository.create_enterprise(profile=profile, trace_id=str(uuid4()))
        seed_result.enterprises[code] = created.id

    return seed_result


def _wire_value(value: str | None, unit: str) -> str | bool | None:
    """CSV 里的布尔写成 ``true``/``false``，契约要真布尔；空值是「未知」，保持 null。"""

    if value is None:
        return None
    if unit == "boolean":
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        raise ValueError(f"布尔事实的值「{value}」既不是 true 也不是 false")
    return value


def run_case(
    dataset: Dataset,
    seed_result: Seed,
    repository: PolicyRepository,
    case: Case,
    *,
    as_of: datetime,
    llm: LlmClient | None,
) -> dict[str, Any]:
    """跑一次判断，把结果折成与基线同形的逐条件记录。"""

    seeded = seed_result.policies[(case.policy_code, case.document)]
    request = AssessmentInput.model_validate(
        {
            "enterpriseId": str(seed_result.enterprises[case.enterprise]),
            "policyIds": [str(seeded.policy_id)],
            "asOf": as_of.isoformat(),
            "period": {"startDate": PERIOD_START.isoformat(), "endDate": PERIOD_END.isoformat()},
        }
    )
    context = AgentContext(
        trace_id=str(uuid4()), as_of=request.as_of, period=(PERIOD_START, PERIOD_END)
    )
    run = assess(request=request, context=context, repository=repository, llm=llm)

    assessment = run.assessments[0]
    # 过期标记取**读取时**的有效性视图：``assess`` 写入时的 ``staleReasons`` 按设计
    # 只看「记录之后发生的事件」，因此永远不含过期；用户在页面上看到的是
    # ``get_run`` 重算的那一版（``storage.py`` 的 ``resolve_validity`` 同一入口）。
    # 两边都用同一个函数，比较的才是「方案会不会告诉用户这项政策已经过期」。
    policy = dataset.policies[(case.policy_code, case.document)]
    boundary = versioning.PolicyBoundary(
        effective_to=policy.effective_to, window_closes_at=_window_close(policy)
    )
    validity = versioning.resolve_validity(
        boundary=boundary,
        events=[],
        criterion_dependencies={},
        run_created_at=run.created_at,
        now=as_of,
        valid_until_at=versioning.valid_until(boundary=boundary, now=as_of),
    )
    results: list[dict[str, Any]] = []
    for item in assessment.criteria_results:
        code = seeded.code_for(item.criterion_id)
        if code is None:
            raise RuntimeError(
                f"{case.case_id} 出现了灌库时没记录的条件 {item.criterion_id}，"
                "代号对不上，指标会错"
            )
        quotes = [ref.quote for ref in item.evidence_refs if ref.quote]
        results.append(
            {
                "criterionId": code,
                "result": item.result,
                "reason": item.reason,
                # 只取第一句：``quote`` 要能在原文里逐字找到，拼接后的串永远对不上，
                # 会把自己算成「引用不实」。
                "quote": quotes[0] if quotes else None,
                "facts": [
                    {
                        "key": ref.key,
                        "period": f"{ref.period.start_date.isoformat()}/"
                        f"{ref.period.end_date.isoformat()}",
                    }
                    for ref in item.fact_refs
                ],
            }
        )
    codes = [item["code"] for item in validity.stale_reasons]
    return {
        "results": results,
        "validity": validity.validity,
        "policyExpired": any(
            code in {"policy_expired", "window_closed"} for code in codes
        ),
        "staleReasons": codes,
        "summary": assessment.explanation,
    }


def _window_close(policy: Policy) -> datetime | None:
    """与灌库时写的 ``applicationWindow.closesAt`` 保持一致（当日 23:59:59 北京时间）。"""

    if policy.application_close is None:
        return None
    return datetime.combine(policy.application_close, time(23, 59, 59), SHANGHAI)


def run(
    dataset: Dataset,
    seed_result: Seed,
    repository: PolicyRepository,
    *,
    as_of: datetime,
    llm: LlmClient | None,
    only: Sequence[str] | None = None,
    limit: int | None = None,
) -> Iterator[tuple[Case, dict[str, Any]]]:
    cases: Sequence[Case] = dataset.cases
    if only:
        wanted = {code.strip().upper() for code in only}
        cases = [case for case in cases if case.case_id in wanted]
        missing = wanted - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"没有这些 case：{'、'.join(sorted(missing))}")
    for index, case in enumerate(cases, start=1):
        if limit is not None and index > limit:
            break
        yield case, run_case(dataset, seed_result, repository, case, as_of=as_of, llm=llm)


@dataclass(slots=True)
class Meter:
    """套在真实客户端外面，记一次判断里解释阶段用掉的量。

    ``assess`` 只在解释阶段调模型，一处一处累计比事后从日志里捞准确。
    """

    inner: LlmClient

    prompt: int = 0
    completion: int = 0
    elapsed_ms: int = 0
    calls: int = 0
    unknown: int = 0

    @property
    def mode(self) -> str:
        return self.inner.mode

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def complete(self, *, system: str, user: str, max_tokens: int = 1024):
        result = self.inner.complete(system=system, user=user, max_tokens=max_tokens)
        self.calls += 1
        self.elapsed_ms += result.elapsed_ms
        if result.usage_known:
            self.prompt += result.prompt_tokens
            self.completion += result.completion_tokens
        else:
            self.unknown += 1
        return result

    def reset(self) -> None:
        self.prompt = self.completion = self.elapsed_ms = self.calls = self.unknown = 0


def outcomes(
    dataset: Dataset,
    seed_result: Seed,
    repository: PolicyRepository,
    *,
    as_of: datetime,
    llm: LlmClient | None,
    only: Sequence[str] | None = None,
    limit: int | None = None,
) -> Iterator[CaseOutcome]:
    """逐 case 跑并折成与基线同形的记录；模型故障不中断整轮，记成该 case 失败。"""

    from .baseline import CaseOutcome

    meter = Meter(inner=llm) if llm is not None else None
    cases: Sequence[Case] = dataset.cases
    if only:
        wanted = {code.strip().upper() for code in only}
        cases = [case for case in cases if case.case_id in wanted]
        missing = wanted - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"没有这些 case：{'、'.join(sorted(missing))}")
    for index, case in enumerate(cases, start=1):
        if limit is not None and index > limit:
            break
        if meter is not None:
            meter.reset()
        try:
            payload = run_case(
                dataset, seed_result, repository, case, as_of=as_of, llm=meter
            )
        except ApiProblem as exc:
            yield CaseOutcome(
                case_id=case.case_id,
                enterprise=case.enterprise,
                policy_code=case.policy_code,
                document=case.document,
                outcome="api_error",
                attempts=1,
                model=meter.model_name if meter is not None else "rule-only",
                prompt_version=RULE_VERSION,
                elapsed_ms=meter.elapsed_ms if meter is not None else 0,
                usage_prompt=meter.prompt if meter is not None else 0,
                usage_completion=meter.completion if meter is not None else 0,
                usage_known=meter is None or meter.unknown == 0,
                payload=None,
                error=f"{exc.code}: {exc.message}",
            )
            continue
        yield CaseOutcome(
            case_id=case.case_id,
            enterprise=case.enterprise,
            policy_code=case.policy_code,
            document=case.document,
            outcome="ok",
            attempts=1,
            model=meter.model_name if meter is not None else "rule-only",
            prompt_version=RULE_VERSION,
            elapsed_ms=meter.elapsed_ms if meter is not None else 0,
            usage_prompt=meter.prompt if meter is not None else 0,
            usage_completion=meter.completion if meter is not None else 0,
            usage_known=meter is None or meter.unknown == 0,
            payload=payload,
            error=None,
        )


@contextmanager
def temporary_repository(
    dsn: str, schema: str, *, cursor_secret: str
) -> Iterator[PolicyRepository]:
    """在 ``schema`` 下跑完全部迁移，产出一个仓储；退出时整库删掉。

    灌的是**临时 schema**，不是 ``public``：这里会真的写入政策与企业，跑到线上库
    上就等于凭空多出 6 项合成政策。迁移连跑两遍，与测试库里验证可重复执行的做法
    一致（``infra/sql`` 全部 ``IF NOT EXISTS`` / ``ON CONFLICT``）。
    """

    import psycopg

    scripts = [
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "infra" / "sql").glob("*.sql"))
    ]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.execute(f"CREATE SCHEMA {schema}")

    scoped = dsn + ("&" if "?" in dsn else "?") + f"options=-csearch_path%3D{schema}"
    with psycopg.connect(scoped, autocommit=True) as conn:
        for script in scripts:
            conn.execute(script)
        for script in scripts:
            conn.execute(script)

    database = Database(scoped)
    try:
        yield PolicyRepository(database, cursor_secret=cursor_secret)
    finally:
        database.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _resolve_dsn(explicit: str | None) -> str:
    """库地址只认评估专用变量。

    不读 ``DATABASE_URL``：那是服务自己的库，误把合成政策灌进去是污染，不是配置错误。
    """

    import os

    dsn = (explicit or os.environ.get("HUICE_EVAL_DATABASE_URL") or "").strip()
    if not dsn:
        raise SystemExit(
            "缺少库地址：设 HUICE_EVAL_DATABASE_URL 或用 --dsn 传。"
            "它必须指向可建 schema 的空闲库，脚本会建临时 schema 并在结束时删掉。"
        )
    if dsn == os.environ.get("DATABASE_URL", "").strip():
        raise SystemExit("HUICE_EVAL_DATABASE_URL 与服务端 DATABASE_URL 相同，拒绝灌库。")
    return dsn


def main(
    argv: Sequence[str] | None = None,
    *,
    dataset: Dataset | None = None,
    llm: LlmClient | None = None,
    stream: Any | None = None,
) -> int:
    """``python -m app.ablation pipeline --out FILE``。"""

    import argparse
    import sys

    from .baseline import write_jsonl

    parser = argparse.ArgumentParser(
        prog="python -m app.ablation pipeline", description=__doc__
    )
    parser.add_argument("--out", type=Path, help="JSONL 输出路径；不给就打到 stdout")
    parser.add_argument("--case", action="append", help="只跑指定 case，可重复")
    parser.add_argument("--limit", type=int, help="只跑前 N 个 case（冒烟用）")
    parser.add_argument("--data", type=Path, help="data/ 目录，默认仓库根下的 data")
    parser.add_argument("--dsn", help="临时库连接串；默认读 HUICE_EVAL_DATABASE_URL")
    parser.add_argument(
        "--schema", default=f"huice_ablation_{os.getpid()}", help="临时 schema 名"
    )
    parser.add_argument(
        "--as-of", dest="as_of", help="判断时点 YYYY-MM-DD（北京时间正午），默认今天"
    )
    args = parser.parse_args(argv)

    from .dataset import load_dataset

    data = dataset if dataset is not None else load_dataset(args.data)
    as_of = (
        datetime.fromisoformat(args.as_of).replace(tzinfo=SHANGHAI, hour=12)
        if args.as_of
        else default_as_of()
    )
    dsn = _resolve_dsn(args.dsn)

    with temporary_repository(dsn, args.schema, cursor_secret="evaluation") as repository:
        seed_result = seed(data, repository)
        records = list(
            outcomes(
                data,
                seed_result,
                repository,
                as_of=as_of,
                llm=llm,
                only=args.case,
                limit=args.limit,
            )
        )

    model = llm.model_name if llm is not None else "rule-only"
    extra = {"as_of": as_of.isoformat()}
    if stream is not None or args.out is None:
        count = write_jsonl(
            stream or sys.stdout,
            data,
            records,
            scheme="pipeline",
            model=model,
            prompt_version=RULE_VERSION,
            extra=extra,
        )
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as handle:
            count = write_jsonl(
                handle,
                data,
                records,
                scheme="pipeline",
                model=model,
                prompt_version=RULE_VERSION,
                extra=extra,
            )

    failures = [item for item in records if item.outcome != "ok"]
    print(f"完成 {count} 个 case，其中失败 {len(failures)} 个", file=sys.stderr)
    for item in failures:
        print(f"  {item.case_id} {item.outcome}：{item.error}", file=sys.stderr)
    return 0 if not failures else 2
