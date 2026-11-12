"""政策发现运行器的真实数据库用例（E2 退出门槛的机器部分）。

退出门槛三条，这里逐条落成断言：

1. **未提交候选不进入政策库**：跑完几轮之后 ``policy`` / ``policy_version`` /
   ``criterion`` 仍是 0 行——候选只活在暂存区；
2. **下一轮过期正确**：第二轮开始时上一轮的待处理候选变 ``expired``，已提交的候选
   不受影响（后者在 ``test_discovery_http.py`` 里配合提交链路验）；
3. **失败与新政策可分辨**：``succeeded/no_new_policy`` 与 ``failed/fetch_failed``
   是两个可机器判断的组合，不需要解析 detail 文案。

并发与锁也在这里验：另开一条连接拿住 ``pg_try_advisory_lock`` 时，本轮直接返回
``locked`` 且一个字节都不写；进程崩溃留下的 ``running`` 行由下一轮持锁后收成
``failed/lock_lost``。

仅在设置 ``HUICE_TEST_DATABASE_URL`` 时运行；每个模块用独立 schema，结束即删。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from app.discovery import runner as runner_module
from app.discovery.extraction import CODE_NO_CRITERIA
from app.discovery.fetch import FetchError, estimate_tokens
from app.discovery.runner import (
    CANDIDATE_TOKEN_BUDGET,
    MAX_COMPLETION_TOKENS,
    PROMPT_OVERHEAD_TOKENS,
)
from app.integrations.discovery import DiscoveryRepository
from db_support import query_all, query_one
from discovery_support import (
    ARTICLE_PAGE,
    ARTICLE_URL,
    BODY,
    QUOTE,
    EmptyLlm,
    FakeFetcher,
    FakeLlm,
    FailingLlm,
    LIST_PAGE,
    LIST_URL,
    NOW,
    article_pages,
    configure,
    make_runner,
    page,
)

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


@pytest.fixture(autouse=True)
def discovery_settings_row(database_url_schema):
    """``clean_tables`` 会清掉迁移里预置的那行配置；每个用例先把它补回来。

    生产库里这行由迁移 010 写入 ``ON CONFLICT DO NOTHING``；测试 schema 在用例之间被
    TRUNCATE，所以这里按同一口径重放一次。
    """

    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO discovery_settings (id, revision) "
            "VALUES (1, gen_random_uuid()) ON CONFLICT (id) DO NOTHING"
        )


@pytest.fixture
def discovery(db) -> DiscoveryRepository:
    return DiscoveryRepository(db, cursor_secret="runner-test")


def _run_row(dsn: str, run_id) -> tuple:
    return query_one(
        dsn,
        "SELECT status, terminal_reason, new_candidates, duplicates, failed_sources, "
        "tokens_used, late_by_seconds, finished_at FROM discovery_run WHERE id = %s",
        (run_id,),
    )


def _candidates(dsn: str) -> list[tuple]:
    return query_all(
        dsn,
        "SELECT status, source_url, content_hash, version, duplicate_of, existing_version "
        "FROM policy_candidate ORDER BY created_at",
    )


def _policy_rows(dsn: str) -> tuple[int, int, int]:
    return (
        query_one(dsn, "SELECT count(*) FROM policy")[0],
        query_one(dsn, "SELECT count(*) FROM policy_version")[0],
        query_one(dsn, "SELECT count(*) FROM policy_criterion")[0],
    )


# ------------------------------------------------------------------ 到点判断


def test_disabled_settings_do_nothing(db, discovery, database_url_schema):
    configure(discovery, enabled=False)
    outcome = make_runner(db, discovery, FakeFetcher({})).run()

    assert outcome.reason == "disabled"
    assert outcome.ran is False
    assert query_one(database_url_schema, "SELECT count(*) FROM discovery_run")[0] == 0


def test_a_run_before_its_due_time_writes_no_run_row(db, discovery, database_url_schema):
    """timer 每 15 分钟唤醒一次；没到点就什么都不记，否则列表会被空转淹没。"""

    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    holder = {"now": NOW}
    runner = make_runner(db, discovery, fetcher, llm=FakeLlm(), holder=holder)

    first = runner.run(trigger="timer")
    assert first.reason == "completed"

    holder["now"] = NOW + timedelta(minutes=5)
    second = runner.run(trigger="timer")
    assert second.ran is False and second.reason == "not_due"
    assert query_one(database_url_schema, "SELECT count(*) FROM discovery_run")[0] == 1


# ------------------------------------------------------------------ 正常一轮


def test_a_successful_run_stages_candidates_without_touching_the_policy_library(
    db, discovery, database_url_schema
):
    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    outcome = make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    assert outcome.reason == "completed"
    assert outcome.new_candidates == 1

    status, reason, new, duplicates, failed, tokens, late_by, finished = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("succeeded", "completed")
    assert (new, duplicates, failed) == (1, 0, 0)
    assert tokens == 130, "已知用量按输入+输出记账"
    assert late_by is None and finished is not None

    rows = _candidates(database_url_schema)
    assert len(rows) == 1
    candidate_status, source_url, content_hash, version, duplicate_of, existing = rows[0]
    assert candidate_status == "pending_review"
    assert source_url == ARTICLE_URL
    assert len(content_hash) == 64
    # 提交到哪条政策/用哪个版本号由审核员决定，抓取器不猜。
    assert version is None and duplicate_of is None and existing is None

    # 退出门槛 1：候选只在暂存区。
    assert _policy_rows(database_url_schema) == (0, 0, 0)

    # next_run_at 按周期推进，而不是按「距上次运行」。
    settings = discovery.get_settings()
    assert settings["next_run_at"] == NOW + timedelta(minutes=1440)


def test_candidate_keeps_provenance_fields(db, discovery, database_url_schema):
    """每个候选都带抓取时间、来源 URL 与原文哈希——审核员据此判断证据是否可信。"""

    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    fetched_at, raw_hash, body_text, extraction = query_one(
        database_url_schema,
        "SELECT fetched_at, raw_hash, body_text, extraction FROM policy_candidate",
    )
    assert fetched_at == NOW
    assert len(raw_hash) == 64
    assert QUOTE in body_text
    assert extraction["modelVersion"] == "deepseek-chat"
    assert extraction["criteriaCount"] == 1


def test_catch_up_records_late_seconds_once(db, discovery, database_url_schema):
    """停机后补跑一次并记迟到秒数；不追历史每一轮。"""

    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    holder = {"now": NOW}
    runner = make_runner(db, discovery, fetcher, llm=FakeLlm(), holder=holder)
    runner.run(trigger="timer")

    # 第一轮把 next_run_at 推到 NOW+1440 分钟；停机三小时后才醒，按到点判断照跑一次。
    holder["now"] = NOW + timedelta(minutes=1440, hours=3)
    outcome = runner.run(trigger="timer")
    assert outcome.ran is True
    late_by = query_one(
        database_url_schema, "SELECT late_by_seconds FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )[0]
    assert late_by == 3 * 3600


# ------------------------------------------------------------------ 轮换与区分


def test_the_next_run_expires_previous_candidates_and_says_no_new_policy(
    db, discovery, database_url_schema
):
    """退出门槛 2 与 3：轮换只动待处理候选，且「没有新政策」不是失败。"""

    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    holder = {"now": NOW}
    runner = make_runner(db, discovery, fetcher, llm=FakeLlm(), holder=holder)

    first = runner.run(trigger="timer")
    assert first.reason == "completed"

    holder["now"] = NOW + timedelta(days=1)
    second = runner.run(trigger="timer")

    assert second.reason == "no_new_policy"
    assert second.ran is True
    assert second.duplicates >= 1

    status, reason, new, duplicates, *_ = _run_row(database_url_schema, second.run_id)
    assert (status, reason) == ("succeeded", "no_new_policy")
    assert new == 0 and duplicates >= 1

    statuses = [row[0] for row in _candidates(database_url_schema)]
    assert statuses == ["expired"], "上一轮的待处理候选应随本轮轮换过期"
    assert _policy_rows(database_url_schema) == (0, 0, 0)


def test_fetch_failure_is_a_different_terminal_state_than_no_new_policy(
    db, discovery, database_url_schema
):
    configure(discovery)
    fetcher = FakeFetcher(
        {}, errors={LIST_URL: FetchError("timeout", "单页请求超时。")}
    )
    outcome = make_runner(db, discovery, fetcher).run(trigger="timer")

    assert outcome.reason == "timeout"
    status, reason, new, _duplicates, failed, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("failed", "timeout")
    assert new == 0 and failed == 1
    assert _candidates(database_url_schema) == []


def test_all_sources_failing_with_mixed_reasons_degrades_to_fetch_failed(
    db, discovery, database_url_schema
):
    configure(discovery, sources=(LIST_URL, "https://www.most.gov.cn/kjbgz/"))
    fetcher = FakeFetcher(
        {},
        errors={
            LIST_URL: FetchError("timeout", "单页请求超时。"),
            "https://www.most.gov.cn/kjbgz/": FetchError("connect_failed", "连接失败。"),
        },
    )
    outcome = make_runner(db, discovery, fetcher).run(trigger="timer")

    status, reason, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("failed", "fetch_failed")


def test_one_failed_source_among_ok_ones_is_partial_failure(
    db, discovery, database_url_schema
):
    configure(discovery, sources=(LIST_URL, "https://www.most.gov.cn/kjbgz/"))
    fetcher = FakeFetcher(
        {LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE},
        errors={"https://www.most.gov.cn/kjbgz/": FetchError("http_error", "HTTP 503。")},
    )
    outcome = make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("partial_failed", "partial_failed")
    assert new == 1, "失败来源不影响其它来源的结果"


def test_every_article_failing_is_a_failure_not_no_new_policy(
    db, discovery, database_url_schema
):
    """栏目页拿到了、正文一篇都没下来：必须是失败，不能报「没有新政策」。

    DISC-AT-07 要求「无新政策」与「全失败」有各自可分辨的真实反馈。过去正文抓取的
    ``FetchError`` 被 ``continue`` 直接吞掉，这种轮次会记成 ``succeeded/no_new_policy``
    ——审核员看到的是「今天没有新政」，实际上是抓取链路整体不通。
    """

    configure(discovery)
    pages, _links = article_pages(2)
    article_urls = sorted(url for url in pages if url != LIST_URL)
    fetcher = FakeFetcher(
        {LIST_URL: pages[LIST_URL]},
        errors={url: FetchError("timeout", "正文页请求超时。") for url in article_urls},
    )
    outcome = make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    status, reason, new, _duplicates, failed_sources, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("failed", "timeout")
    assert new == 0
    assert failed_sources == 1, "正文全挂的来源按失败来源记"

    run = query_one(
        database_url_schema,
        "SELECT failed_articles, source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )
    assert run[0] == 2
    assert run[1][0]["articleFailures"] == 2
    assert run[1][0]["status"] == "failed"
    assert _candidates(database_url_schema) == []


def test_some_articles_failing_is_partial_failure_with_the_rest_kept(
    db, discovery, database_url_schema
):
    """部分正文失败：其余照常入库，整轮记 partial_failed，失败篇数可读出来。"""

    configure(discovery)
    pages, _links = article_pages(2)
    article_urls = sorted(url for url in pages if url != LIST_URL)
    fetcher = FakeFetcher(
        pages, errors={article_urls[1]: FetchError("network", "连接被重置。")}
    )
    outcome = make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("partial_failed", "partial_failed")
    assert new == 1, "抓到的另一篇照常成为候选"

    run = query_one(
        database_url_schema,
        "SELECT failed_articles, source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )
    assert run[0] == 1
    assert run[1][0]["status"] == "ok", "只有部分正文失败时来源本身不算失败"
    assert run[1][0]["articleFailures"] == 1


def test_a_clean_run_reports_zero_article_failures(db, discovery, database_url_schema):
    configure(discovery)
    fetcher = FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE})
    outcome = make_runner(db, discovery, fetcher, llm=FakeLlm()).run(trigger="timer")

    status, reason, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("succeeded", "completed")
    assert (
        query_one(
            database_url_schema,
            "SELECT failed_articles FROM discovery_run WHERE id = %s",
            (outcome.run_id,),
        )[0]
        == 0
    )


def test_an_empty_source_list_records_one_quiet_round(db, discovery, database_url_schema):
    """已启用但没配来源：记一条成功的空轮次并推进 next_run_at，不让 timer 反复重试。"""

    configure(discovery, sources=())
    outcome = make_runner(db, discovery, FakeFetcher({})).run(trigger="timer")

    assert outcome.reason == "no_new_policy"
    status, reason, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("succeeded", "no_new_policy")
    assert discovery.get_settings()["next_run_at"] == NOW + timedelta(minutes=1440)


# ------------------------------------------------------------------ 预算


def test_a_single_oversized_page_fails_only_that_candidate(db, discovery, database_url_schema):
    """单候选超预算是「这一条要人工看」，不是整轮失败——把两者混起来会让一次超长
    页面顶掉整轮本来能入库的候选。"""

    long_body = BODY + ("补充说明。" * (CANDIDATE_TOKEN_BUDGET))
    assert len(long_body) / 1.5 + 64 > CANDIDATE_TOKEN_BUDGET
    pages = {LIST_URL: LIST_PAGE, ARTICLE_URL: page(ARTICLE_URL, long_body)}
    configure(discovery)

    llm = FakeLlm()
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=llm).run(trigger="timer")

    assert llm.calls == 0, "超预算的正文不应再发模型请求"
    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("succeeded", "completed")
    assert new == 1
    row = query_one(
        database_url_schema, "SELECT status, extraction FROM policy_candidate"
    )
    assert row[0] == "extraction_failed"
    assert row[1]["failureCode"] == "EXTRACTION_BUDGET_EXCEEDED"


def test_the_run_budget_reserves_the_completion_upper_bound(
    db, discovery, database_url_schema, monkeypatch
):
    """余额只够输入时不该发调用：输出上限也算进这一篇的预留。

    #64 评审 P1 的回归用例。旧实现只比 ``tokens_used + estimate``，把调用放出去之后
    实际还会消耗最多 ``max_tokens`` 的输出，整轮因此能越过上限。
    """

    configure(discovery)
    estimate = estimate_tokens(ARTICLE_PAGE.text)
    # 预算刚好装下输入，但装不下「输入 + 提示余量 + 输出上限」。
    monkeypatch.setattr(
        runner_module,
        "RUN_TOKEN_BUDGET",
        estimate + PROMPT_OVERHEAD_TOKENS + MAX_COMPLETION_TOKENS - 1,
    )

    llm = FakeLlm()
    outcome = make_runner(
        db,
        discovery,
        FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE}),
        llm=llm,
    ).run(trigger="timer")

    assert llm.calls == 0, "预留装不下时不应发出模型调用"
    status, reason, _new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("failed", "budget_exceeded")
    assert query_one(database_url_schema, "SELECT count(*) FROM policy_candidate")[0] == 0


def test_a_page_with_attachments_carries_an_explicit_review_note(
    db, discovery, database_url_schema
):
    """附件首版不抓取，就必须在候选上写明——不能让审核员以为正文里已经没有条件了。"""

    configure(discovery)
    attachment = "https://www.gov.cn/zhengce/content/2026-11/12/shouce.pdf"
    pages = {
        LIST_URL: LIST_PAGE,
        ARTICLE_URL: page(ARTICLE_URL, BODY, attachments=(attachment,)),
    }
    make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    findings, urls = query_one(
        database_url_schema, "SELECT validation_findings, attachment_urls FROM policy_candidate"
    )
    assert [item["code"] for item in findings] == ["ATTACHMENT_NOT_EXTRACTED"]
    assert findings[0]["criterionId"] is None
    assert "附件" in findings[0]["message"]
    assert urls == [attachment], "附件地址仍要留档，供人工打开"


def test_extraction_without_criteria_stages_an_explicit_failure(
    db, discovery, database_url_schema
):
    configure(discovery)

    outcome = make_runner(db, discovery, FakeFetcher(
        {LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE}
    ), llm=EmptyLlm()).run(trigger="timer")

    assert outcome.reason == "completed"
    status, extraction = query_one(
        database_url_schema, "SELECT status, extraction FROM policy_candidate"
    )
    assert status == "extraction_failed"
    assert extraction["failureCode"] == CODE_NO_CRITERIA


def test_a_model_error_stops_the_round_instead_of_multiplying_failures(
    db, discovery, database_url_schema
):
    """模型故障时停手：继续调用只会把一次故障放大成几十次。"""

    configure(discovery)
    outcome = make_runner(
        db,
        discovery,
        FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE}),
        llm=FailingLlm(),
    ).run(trigger="timer")

    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("partial_failed", "partial_failed")
    assert new == 1, "失败的候选本身仍要留档，审核员才知道哪条没抽出来"


# ------------------------------------------------------------------ 并发与锁


def test_another_process_holding_the_lock_makes_this_round_a_no_op(
    db, discovery, database_url_schema
):
    from app.discovery.runner import LOCK_SQL

    configure(discovery)
    with psycopg.connect(database_url_schema, autocommit=True) as holder:
        assert holder.execute(LOCK_SQL).fetchone()[0] is True
        outcome = make_runner(
            db, discovery, FakeFetcher({LIST_URL: LIST_PAGE})
        ).run(trigger="timer")

    assert outcome.ran is False and outcome.reason == "locked"
    assert query_one(database_url_schema, "SELECT count(*) FROM discovery_run")[0] == 0
    assert discovery.get_settings()["next_run_at"] is None, "没跑成就不该推进计划时间"


def test_a_crashed_run_is_closed_as_lock_lost_by_the_next_one(
    db, discovery, database_url_schema
):
    """进程被杀会留下 running 行；持锁后可以断定没有别人在跑，于是收成 lock_lost。"""

    configure(discovery)
    stale_id = uuid4()
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO discovery_run (id, trigger, started_at, status) "
            "VALUES (%s, 'timer', %s, 'running')",
            (stale_id, NOW - timedelta(hours=2)),
        )

    outcome = make_runner(
        db, discovery, FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE}),
        llm=FakeLlm(),
    ).run(trigger="timer")

    assert outcome.reason == "completed"
    status, reason, *_ = _run_row(database_url_schema, stale_id)
    assert (status, reason) == ("failed", "lock_lost")


def test_run_budget_is_enforced_across_candidates(db, discovery, database_url_schema):
    """整轮预算用尽就中止后续候选并记 ``budget_exceeded``（与「单候选超预算」不同）。"""

    pages, articles = article_pages(4)
    configure(discovery)

    # 每条候选模型报 20,000 token：第二、三条之后累计就会撞上 50,000 的运行预算。
    llm = FakeLlm(tokens=20_000)
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=llm).run(trigger="timer")

    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("partial_failed", "budget_exceeded")
    assert 0 < new < len(articles), "预算用尽前抽到的候选要保留"
    assert llm.calls == new
