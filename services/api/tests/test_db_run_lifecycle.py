"""运行生命周期（E3 B2 / D5）的真实数据库用例。

三件事必须成立：

1. 评估开始前就有一行 ``running``（T1），评估成功才写结果并转 ``succeeded``（T2），
   失败只写状态与分类（T3）——结果行与「成功」不允许出现中间态。
2. 未成功完成的 run 能查状态但拿不到结果：API-12 从「一律 404」改成 409。
3. 中断扫描的三条判据缺一不可：超时、锁未被持有、原连接已断开。少任何一条都会把
   仍在跑的运行标死，这类误标比漏标难发现得多。

仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行。
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from app.agents.contracts import AgentContext
from app.agents.orchestrator import assess
from app.agents.profile import normalize_profile
from app.domain.versioning import SHANGHAI
from app.errors import ApiProblem
from app.integrations.storage import CriterionRecord, PolicyRepository
from app.schemas import AssessmentInput, EnterpriseInput
from db_support import create, query_all, query_one

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

YEAR = {"startDate": "2026-01-01", "endDate": "2026-12-31"}
PERIOD = (date(2026, 1, 1), date(2026, 12, 31))
AS_OF = datetime(2026, 10, 23, 10, 0, tzinfo=SHANGHAI)
FACT_VERSION = "f" * 64

INDUSTRY_GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}


def _enterprise(repo):
    profile = normalize_profile(
        EnterpriseInput.model_validate(
            {
                "nameSynthetic": "合成企业A",
                "period": YEAR,
                "facts": [
                    {
                        "key": "region",
                        "value": "440300",
                        "unit": "code",
                        "period": YEAR,
                        "source": "user_input",
                    },
                    {
                        "key": "industry",
                        "value": "C39",
                        "unit": "code",
                        "period": YEAR,
                        "source": "user_input",
                    },
                ],
            }
        )
    )
    return repo.create_enterprise(profile=profile, trace_id=str(uuid4()))


def _publish(repo, *, version: str = "v1.0"):
    draft = create(repo, version=version)
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
    saved = repo.save_criteria(
        policy_id=draft.id,
        version=version,
        expected_revision=draft.revision,
        criteria=[
            CriterionRecord(
                id=uuid4(),
                title="演示条件",
                expression=INDUSTRY_GATE,
                spans=spans,
                review_finding="none",
                validation_status="passed",
            )
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


def _assess(repo, enterprise_id, policy_ids):
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


# ------------------------------------------------------------------ T1 / T2


def test_the_running_row_exists_before_the_results(
    database_url_schema: str, repo, monkeypatch
) -> None:
    """T1 先提交：评估过程中库里就有 running 行，且锁持有者已记下。"""

    enterprise = _enterprise(repo)
    policy = _publish(repo)
    seen: dict[str, object] = {}

    original = repo.published_policy_snapshots

    def spy(policy_ids):
        # 评估正中间读库：这时必须已经是 running，而不是等结果写完才出现
        seen["status"] = query_one(
            database_url_schema, "SELECT status FROM assessment_run"
        )[0]
        seen["lock"] = query_one(
            database_url_schema,
            "SELECT lock_owner_pid, lock_owner_started_at IS NOT NULL FROM assessment_run",
        )
        return original(policy_ids)

    monkeypatch.setattr(repo, "published_policy_snapshots", spy)

    run = _assess(repo, enterprise.id, [policy.id])

    assert seen["status"] == "running"
    assert seen["lock"][0] is not None
    assert seen["lock"][1] is True

    row = query_one(
        database_url_schema,
        "SELECT id, status, finished_at, error_category, created_at "
        "FROM assessment_run WHERE id = %s",
        (run.id,),
    )
    assert row[1] == "succeeded"
    assert row[2] is not None
    assert row[3] is None
    # created_at 是受理时刻（T1），不是完成时刻；finished_at 才是完成时刻
    assert row[4] == run.created_at


def test_a_failed_run_keeps_its_row_and_writes_no_results(
    database_url_schema: str, repo
) -> None:
    """T3：未发布的政策 → 失败行 + 分类，结果表一行不写，异常照旧抛给调用方。"""

    enterprise = _enterprise(repo)
    draft = create(repo, version="v1.0")  # 只有草稿，没有发布

    with pytest.raises(ApiProblem) as caught:
        _assess(repo, enterprise.id, [draft.id])
    assert caught.value.code == "POLICY_NOT_PUBLISHED"

    row = query_one(
        database_url_schema,
        "SELECT status, error_category, finished_at IS NOT NULL FROM assessment_run",
    )
    assert row == ("failed", "policy_not_published", True)
    assert query_all(database_url_schema, "SELECT * FROM assessment") == []


# ------------------------------------------------------------------ API-12


def test_get_run_distinguishes_unfinished_from_missing(database_url_schema: str, repo, db) -> None:
    enterprise = _enterprise(repo)
    run_id = uuid4()
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO assessment_run "
            "(id, enterprise_id, as_of, status, fact_version, model_or_rule_version, "
            " period_start, period_end) "
            "VALUES (%s, %s, now(), 'running', %s, 'rule:criteria@1', %s, %s)",
            (run_id, enterprise.id, FACT_VERSION, PERIOD[0], PERIOD[1]),
        )
        conn.commit()

    # 默认（契约窗口未打开）保持既有行为：没有结果快照就按「没有结果」报 404，
    # 不新造 409——新语义只随契约窗口一起开。
    with pytest.raises(ApiProblem) as legacy:
        repo.get_run(run_id)
    assert legacy.value.status_code == 404
    assert legacy.value.code == "RUN_NOT_FOUND"

    strict = PolicyRepository(db, cursor_secret="test", require_completed_status=True)
    with pytest.raises(ApiProblem) as caught:
        strict.get_run(run_id)
    assert caught.value.status_code == 409
    assert caught.value.code == "RUN_NOT_COMPLETED"
    assert caught.value.details["status"] == "running"

    with pytest.raises(ApiProblem) as missing:
        strict.get_run(uuid4())
    assert missing.value.status_code == 404


# ------------------------------------------------------------------ 中断扫描


def _insert_running(dsn: str, enterprise_id, *, minutes_ago: int, pid=None, started=None) -> UUID:
    run_id = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO assessment_run "
            "(id, enterprise_id, as_of, status, fact_version, model_or_rule_version, "
            " created_at, period_start, period_end, lock_owner_pid, lock_owner_started_at) "
            "VALUES (%s, %s, now(), 'running', %s, 'rule:criteria@1', "
            " now() - make_interval(mins => %s), %s, %s, %s, %s)",
            (run_id, enterprise_id, FACT_VERSION, minutes_ago, PERIOD[0], PERIOD[1], pid, started),
        )
        conn.commit()
    return run_id


def test_interrupt_scan_needs_all_three_conditions(database_url_schema: str, repo) -> None:
    enterprise = _enterprise(repo)

    # ① 没有锁持有者：无从判定，保持 running
    no_owner = _insert_running(database_url_schema, enterprise.id, minutes_ago=180)
    # ② 超时、原连接还活着：不标
    live = psycopg.connect(database_url_schema, autocommit=True)
    try:
        pid, started = live.execute(
            "SELECT pg_backend_pid(), (SELECT backend_start FROM pg_stat_activity "
            "WHERE pid = pg_backend_pid())"
        ).fetchone()
        alive_row = _insert_running(
            database_url_schema, enterprise.id, minutes_ago=180, pid=pid, started=started
        )
        # ③ 超时、锁仍被别的连接持有：还在跑，不标
        locked = _insert_running(
            database_url_schema, enterprise.id, minutes_ago=180, pid=999_999, started=AS_OF
        )
        lock_holder = psycopg.connect(database_url_schema, autocommit=True)
        try:
            lock_holder.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (str(locked),)
            )
            # ④ 超时、锁没人持、原连接已不在：这是唯一该被标的形状
            dead = _insert_running(
                database_url_schema, enterprise.id, minutes_ago=180, pid=999_998, started=AS_OF
            )
            # ⑤ 没超时：不标
            fresh = _insert_running(
                database_url_schema, enterprise.id, minutes_ago=5, pid=999_997, started=AS_OF
            )

            marked = repo.mark_interrupted_runs(max_duration_seconds=3600)
        finally:
            lock_holder.close()
    finally:
        live.close()

    assert marked == [dead]
    statuses = dict(
        query_all(database_url_schema, "SELECT id, status FROM assessment_run")
    )
    assert statuses[no_owner] == "running"
    assert statuses[alive_row] == "running"
    assert statuses[locked] == "running"
    assert statuses[dead] == "interrupted"
    assert statuses[fresh] == "running"


def test_the_lifecycle_start_is_written_once(database_url_schema: str, repo) -> None:
    """覆盖起点按列幂等：第一次写、之后不挪动；B3 先建行也照样补得上。"""

    repo.mark_run_lifecycle_start()
    first = query_one(
        database_url_schema,
        "SELECT run_lifecycle_from FROM collection_baseline WHERE id = 'default'",
    )[0]
    assert first is not None

    repo.mark_run_lifecycle_start()
    second = query_one(
        database_url_schema,
        "SELECT run_lifecycle_from FROM collection_baseline WHERE id = 'default'",
    )[0]
    assert second == first


def test_the_lifecycle_start_does_not_touch_the_review_start(database_url_schema: str, repo) -> None:
    """B3 先写了审核起点、B2 后到：只补自己那一列，不覆盖别人的。"""

    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO collection_baseline (id, review_events_from, review_source) "
            "VALUES ('default', now(), 'reviewed_at')"
        )
        conn.commit()
        review_before = conn.execute(
            "SELECT review_events_from, review_source FROM collection_baseline "
            "WHERE id = 'default'"
        ).fetchone()

    repo.mark_run_lifecycle_start()

    with psycopg.connect(database_url_schema) as conn:
        row = conn.execute(
            "SELECT review_events_from, review_source, run_lifecycle_from "
            "FROM collection_baseline WHERE id = 'default'"
        ).fetchone()
    assert (row[0], row[1]) == review_before
    assert row[2] is not None
