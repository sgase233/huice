#!/usr/bin/env python3
"""在**指定库**里造 E3a 的三份对账样本（VIS-AT-01 / 03 / 05）。

只在隔离库或本机库跑，**不要对着演示库跑**：会往政策列表里塞合成政策。脚本先检查是否
已有 `【E3A 样本】` 开头的版本，有就停下，避免重复造数。

造什么：

1. **结果分布（VIS-AT-03）**：一次评估覆盖四态，再改一条事实让这次 run 变成 `stale`。
   **`conflict` 是构造出来的**（真实已发布政策产生不了冲突，评测里 conflict 样本也是 0），
   manifest 里会注明。
2. **逻辑树（VIS-AT-01）**：同一条政策先发布 v1、再发布 v2，各跑一次，记录两次的
   `policyVersionMap`。
3. **轨迹（VIS-AT-05）**：导出结果分布那次 run 的轨迹步骤与阶段（在线）；离线轨迹来自
   发布链路写入的 `trace_step`。

输出 manifest（JSON）交给 M3 对页面数字。用法::

    DATABASE_URL=... /opt/huice/venv/bin/python infra/deploy/seed-e3a-samples.py \\
        --out /root/e3a-samples.json

退出码：0 成功；2 环境或库状态不合法（含「库里已有样本」）；3 写库失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.agents.contracts import AgentContext  # noqa: E402
from app.agents.ingestion import PreparedSource  # noqa: E402
from app.agents.orchestrator import assess  # noqa: E402
from app.agents.profile import normalize_fact_update, normalize_profile  # noqa: E402
from app.db import Database  # noqa: E402
from app.domain.versioning import SHANGHAI  # noqa: E402
from app.integrations.storage import CriterionRecord, PolicyRepository  # noqa: E402
from app.schemas import (  # noqa: E402
    AssessmentInput,
    EnterpriseInput,
    FactUpdate,
    Period,
    PolicyImportMetadata,
)

MARKER = "【E3A 样本】"
YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
AS_OF = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI)
SOURCE_URL = "https://example.gov.cn/e3a-sample/notice.html"


def _fact(key: str, value, unit: str) -> dict:
    return {"key": key, "value": value, "unit": unit, "period": YEAR, "source": "user_input"}


def _sources() -> list[PreparedSource]:
    text = "本段为 E3A 对账样本，不是真实政策。"
    annex = text + "附件"
    return [
        PreparedSource(
            kind="body",
            file_name="e3a-body.txt",
            mime_type="text/plain",
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            byte_size=len(text.encode()),
        ),
        PreparedSource(
            kind="attachment",
            file_name="e3a-annex.txt",
            mime_type="text/plain",
            text=annex,
            content_hash=hashlib.sha256(annex.encode()).hexdigest(),
            byte_size=len(annex.encode()),
        ),
    ]


def _metadata(*, version: str, title: str, policy_id=None) -> PolicyImportMetadata:
    payload = {
        "version": version,
        "title": MARKER + title,
        "type": "grant",
        "issuer": "合成样本",
        "region": "440300",
        "documentNumber": version,
        "applicableEntities": ["企业"],
        "publishedAt": "2026-01-01",
        "effectiveFrom": "2026-01-01",
        "effectiveTo": None,
        "applicationWindow": None,
        "sourceUrl": SOURCE_URL,
        "officialEntryUrl": SOURCE_URL,
    }
    if policy_id is not None:
        payload["policyId"] = str(policy_id)
    return PolicyImportMetadata.model_validate(payload)


def _spans(body) -> list[dict]:
    return [
        {
            "sourceId": str(body.id),
            "sourceUrl": body.source_url,
            "sourceVersion": "sample",
            "page": None,
            "section": "合成样本",
            "quote": "本段为 E3A 对账样本，不是真实政策。",
            "textHash": body.content_hash,
        }
    ]


def _create_version(repo, *, version: str, title: str, policy_id=None):
    sources = _sources()
    draft = repo.create_policy_version(
        metadata=_metadata(version=version, title=title, policy_id=policy_id),
        sources=sources,
        storage_keys=[f"e3a/{version}-{index}.txt" for index in range(len(sources))],
        trace_id=str(uuid4()),
    )
    return draft


def _publish_with(repo, draft, *, version: str, expressions: list[dict]):
    spans = _spans(draft.sources[0])
    saved = repo.save_criteria(
        policy_id=draft.id,
        version=version,
        expected_revision=draft.revision,
        criteria=[
            CriterionRecord(
                id=uuid4(),
                title=f"样本条件 {index + 1}",
                expression=expression,
                spans=spans,
                review_finding="none",
                validation_status="passed",
            )
            for index, expression in enumerate(expressions)
        ],
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
    )
    return repo.publish(
        policy_id=draft.id,
        version=version,
        expected_revision=saved.revision,
        trace_id=str(uuid4()),
    )


def _publish_conflict(repo, dsn: str, *, version: str):
    """构造 `review_finding='conflict'` 的已发布版本。

    `publish()` 会拒绝冲突条款，所以样本库直接写库——manifest 里注明这是构造数据。
    """

    import psycopg

    draft = _create_version(repo, version=version, title="冲突样本（构造）")
    body = draft.sources[0]
    with psycopg.connect(dsn) as conn:
        version_id = conn.execute(
            "SELECT id FROM policy_version WHERE policy_id = %s AND version = %s",
            (draft.id, version),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO policy_criterion (policy_version_id, criterion_id, title, expression, "
            "source_spans, review_finding, validation_status) "
            "VALUES (%s, %s, '构造冲突条款', %s, %s, 'conflict', 'passed')",
            (
                version_id,
                uuid4(),
                json.dumps({"op": "manual_review"}, ensure_ascii=False),
                json.dumps(_spans(body), ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE policy_version SET review_status = 'published', reviewed_at = now(), "
            "revision = gen_random_uuid() WHERE id = %s",
            (version_id,),
        )
        conn.commit()
    return draft


def _run(repo, enterprise_id, policy_ids):
    request = AssessmentInput.model_validate(
        {
            "enterpriseId": str(enterprise_id),
            "policyIds": [str(item) for item in policy_ids],
            "asOf": AS_OF.isoformat(),
            "period": YEAR,
        }
    )
    context = AgentContext(trace_id=str(uuid4()), as_of=request.as_of, period=PERIOD)
    return assess(request=request, context=context, repository=repo, llm=None)


def _counts(assessments) -> dict[str, int]:
    counts = {"satisfied": 0, "not_satisfied": 0, "insufficient_evidence": 0, "conflict": 0}
    for item in assessments:
        counts[item.result] = counts.get(item.result, 0) + 1
    return counts


def seed(dsn: str, out: Path) -> int:
    database = Database(dsn, max_size=2)
    repository = PolicyRepository(database, cursor_secret="e3a-sample-seed")
    try:
        with database.connection() as conn:
            existing = conn.execute(
                "SELECT count(*) FROM policy_version WHERE title LIKE %s", (MARKER + "%",)
            ).fetchone()[0]
        if existing:
            print(
                f"库里已有 {existing} 个 {MARKER} 版本：请换一个干净库，或先手工清理。",
                file=sys.stderr,
            )
            return 2

        enterprise = repository.create_enterprise(
            profile=normalize_profile(
                EnterpriseInput.model_validate(
                    {
                        "nameSynthetic": MARKER + "企业A",
                        "period": YEAR,
                        "facts": [
                            _fact("region", "440300", "code"),
                            _fact("industry", "C39", "code"),
                            _fact("taxableIncome", "3000000", "CNY"),
                        ],
                    }
                )
            ),
            trace_id=str(uuid4()),
        )

        satisfied = _publish_with(
            repository,
            _create_version(repository, version="E3A-001", title="满足样本"),
            version="E3A-001",
            expressions=[
                {
                    "op": "industry_exclusion",
                    "factKey": "industry",
                    "period": "assessment",
                    "excluded": ["F51", "F52"],
                }
            ],
        )
        not_satisfied = _publish_with(
            repository,
            _create_version(repository, version="E3A-002", title="不满足样本"),
            version="E3A-002",
            expressions=[
                {
                    "op": "compare",
                    "factKey": "taxableIncome",
                    "period": "assessment",
                    "comparator": "gte",
                    "value": "9999999999",
                    "unit": "CNY",
                }
            ],
        )
        insufficient = _publish_with(
            repository,
            _create_version(repository, version="E3A-003", title="证据不足样本"),
            version="E3A-003",
            expressions=[{"op": "manual_review"}],
        )
        conflict = _publish_conflict(repository, dsn, version="E3A-004")

        run = _run(
            repository,
            enterprise.id,
            [satisfied.id, not_satisfied.id, insufficient.id, conflict.id],
        )
        counts = _counts(run.assessments)

        # 改一条事实 → 这次 run 的读取视图变 stale（stale 是读取时重算的）
        fact = normalize_fact_update(
            key="taxableIncome",
            update=FactUpdate(
                value="3500000",
                unit="CNY",
                period=Period(start_date=PERIOD[0], end_date=PERIOD[1]),
                source="user_input",
            ),
            profile_period=PERIOD,
        )
        repository.update_fact(
            enterprise_id=enterprise.id,
            fact=fact,
            expected_fact_version=enterprise.fact_version,
            trace_id=str(uuid4()),
        )
        read_at = datetime.now(timezone.utc)
        refreshed = repository.get_run(run.id)
        stale = [item for item in refreshed.assessments if item.validity == "stale"]
        trace = repository.get_trace(run.id)

        # 逻辑树样本：同一条政策先 v1 再 v2，各跑一次
        history = {"policyId": None, "runs": []}
        v1 = _publish_with(
            repository,
            _create_version(repository, version="E3A-GXJS-v1", title="历史版本样本 v1"),
            version="E3A-GXJS-v1",
            expressions=[{"op": "manual_review"}],
        )
        first = _run(repository, enterprise.id, [v1.id])
        v2 = _publish_with(
            repository,
            _create_version(
                repository, version="E3A-GXJS-v2", title="历史版本样本 v2", policy_id=v1.id
            ),
            version="E3A-GXJS-v2",
            expressions=[{"op": "manual_review"}],
        )
        second = _run(repository, enterprise.id, [v1.id])
        history["policyId"] = str(v1.id)
        history["runs"] = [
            {"runId": str(first.id), "asOf": AS_OF.isoformat(), "policyVersionMap": first.policy_version_map},
            {"runId": str(second.id), "asOf": AS_OF.isoformat(), "policyVersionMap": second.policy_version_map},
        ]
        del v2

        manifest = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "readAt": read_at.isoformat(),
            "note": [
                "全部政策标题以【E3A 样本】开头，是合成数据，不是真实政策。",
                "conflict 由构造的 review_finding='conflict' 条款产生（真实已发布政策产生不了冲突）。",
                "stale 是读取时重算的：对账要按 readAt 这个时刻看。",
            ],
            "distribution": {
                "enterpriseId": str(enterprise.id),
                "runId": str(run.id),
                "assessmentsLength": len(refreshed.assessments),
                "counts": _counts(refreshed.assessments),
                "staleCount": len(stale),
                "policyVersionMap": refreshed.policy_version_map,
                "factVersion": refreshed.fact_version,
            },
            "logicTree": history,
            "trace": {
                "runId": str(run.id),
                "stepCount": len(trace.steps),
                "stages": [step.stage for step in trace.steps],
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已生成样本 manifest：{out}")
        print(
            f"四态计数 {manifest['distribution']['counts']}，stale {len(stale)} 条，"
            f"轨迹 {len(trace.steps)} 步。"
        )
        return 0
    except Exception as exc:  # 不回显连接串
        print(f"写库失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 3
    finally:
        database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="造 E3a 对账样本")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印取值）。", file=sys.stderr)
        return 2
    return seed(dsn, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
