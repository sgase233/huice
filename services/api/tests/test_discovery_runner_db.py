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
from app.discovery.fetch import FetchError, body_fingerprint, estimate_tokens
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
from support import MIGRATIONS

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
        "tokens_used, late_by_seconds, finished_at, suspected_duplicates "
        "FROM discovery_run WHERE id = %s",
        (run_id,),
    )


def _candidates(dsn: str) -> list[tuple]:
    return query_all(
        dsn,
        "SELECT status, source_url, content_hash, version, duplicate_of, existing_version, "
        "suspected_duplicate_of FROM policy_candidate ORDER BY created_at",
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

    status, reason, new, duplicates, failed, tokens, late_by, finished, _suspected = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("succeeded", "completed")
    assert (new, duplicates, failed) == (1, 0, 0)
    assert tokens == 130, "已知用量按输入+输出记账"
    assert late_by is None and finished is not None

    rows = _candidates(database_url_schema)
    assert len(rows) == 1
    candidate_status, source_url, content_hash, version, duplicate_of, existing, _suspect = (
        rows[0]
    )
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


def test_a_page_with_attachments_records_the_urls_without_storing_a_note(
    db, discovery, database_url_schema
):
    """附件地址要留档；「附件未抽取」的提示由它派生，不落库。

    存一份提示会与条件级 findings 抢同一个字段：DISC-06 保存条件时会把这一列整体
    重算，副本第一次编辑就没了。派生出来的提示编辑不掉（见 HTTP 用例）。
    """

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
    assert urls == [attachment], "附件地址仍要留档，供人工打开"
    assert "ATTACHMENT_NOT_EXTRACTED" not in [
        item["code"] for item in findings
    ], "提示是派生的，不该再存一份会被编辑覆盖的副本"


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


# ------------------------------------------------- 框架页、跨站同文与内容变化


def test_a_frame_page_is_a_source_failure_not_a_pseudo_candidate(
    db, discovery, database_url_schema
):
    """首批 D 组根页：200、正文为空、没有链接。不能当单篇正文入库。

    老逻辑把「没有链接」等同于「单篇正文入口」，框架页会变成一条 extraction_failed
    伪候选；现在按来源级失败记 ``unsupported_list``，整轮如实报失败。
    """

    configure(discovery)
    frame = page(LIST_URL, "", markup="<html><body><div id='frame'></div></body></html>")
    outcome = make_runner(db, discovery, FakeFetcher({LIST_URL: frame})).run(trigger="timer")

    status, reason, new, _duplicates, failed_sources, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("failed", "fetch_failed")
    assert new == 0 and failed_sources == 1
    source_status = query_one(
        database_url_schema,
        "SELECT source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )[0]
    assert source_status[0]["errorCategory"] == "unsupported_list"
    assert _candidates(database_url_schema) == []


def test_a_frame_page_link_to_an_empty_issue_page_is_an_article_failure(
    db, discovery, database_url_schema
):
    """首批 F 组根页：只有一个「本期目录」链接。它抓下来也是空页，不能当正文。"""

    configure(discovery)
    issue_url = "https://www.gov.cn/gongbao/currentissue.htm"
    root = page(
        LIST_URL, "点击跳转", markup=f'<html><body><a href="{issue_url}">本期目录</a></body></html>'
    )
    empty = page(issue_url, "", markup="<html><body></body></html>")
    outcome = make_runner(db, discovery, FakeFetcher({LIST_URL: root, issue_url: empty})).run(
        trigger="timer"
    )

    status, reason, new, _duplicates, failed_sources, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("failed", "fetch_failed")
    assert new == 0 and failed_sources == 1
    run = query_one(
        database_url_schema,
        "SELECT failed_articles, source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )
    assert run[0] == 1
    assert run[1][0]["articleFailures"] == 1
    assert _candidates(database_url_schema) == []


def test_a_column_root_does_not_fetch_year_or_navigation_indexes(
    db, discovery, database_url_schema
):
    """E 组根页：50 多条官方链接里，年度分类/栏目导航占绝大多数。

    这些目录页正文超过 60 字，旧逻辑会按 DOM 顺序先抓先抽取，既花预算又可能产出
    伪候选。链接范围过滤必须在抓取之前生效：目录链接不进抓取队列。
    """

    root_url = "https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/gfxwj/"
    pages, article_links = article_pages(11)
    article_urls = [url for url in pages if url != LIST_URL]
    listing = [
        ("./gfxwj2026/", "2026年"),
        ("./gfxwj2025/", "2025年"),
        ("./gfxwj2010before/", "2010年之前"),
        ("../../zfxxgkzn/", "政府信息公开指南"),
        ("../../qtwj/", "其他文件"),
        ("/nav/default.html", "更多"),
    ]
    markup = "".join(f'<a href="{href}">{text}</a>' for href, text in listing)
    markup += "".join(article_links)
    pages[root_url] = page(
        root_url, "规范性文件列表", markup=f"<html><body>{markup}</body></html>"
    )

    configure(discovery, sources=(root_url,))
    fetcher = FakeFetcher(pages)
    llm = FakeLlm()
    outcome = make_runner(db, discovery, fetcher, llm=llm).run(trigger="timer")

    assert fetcher.calls[0] == root_url
    assert set(fetcher.calls[1:]) == set(article_urls)
    assert llm.calls == 11
    status, reason, new, duplicates, _failed_sources, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("succeeded", "completed")
    assert (new, duplicates) == (11, 0)
    assert len(_candidates(database_url_schema)) == 11


def test_a_root_that_only_lists_directories_is_a_source_failure(
    db, discovery, database_url_schema
):
    """过滤后一条正文链接都不剩：不能退回「整页当单篇正文」做伪候选。"""

    configure(discovery)
    root = page(
        LIST_URL,
        "规范性文件：" + "目录按年度分类，请选择年份查看。" * 5,
        markup=(
            '<html><body><a href="./gfxwj2026/">2026年</a>'
            '<a href="./gfxwj2025/">2025年</a></body></html>'
        ),
    )
    fetcher = FakeFetcher({LIST_URL: root})
    llm = FakeLlm()
    outcome = make_runner(db, discovery, fetcher, llm=llm).run(trigger="timer")

    status, reason, new, _duplicates, failed_sources, *_ = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("failed", "fetch_failed")
    assert (new, failed_sources) == (0, 1)
    assert fetcher.calls == [LIST_URL]
    assert llm.calls == 0
    assert _candidates(database_url_schema) == []
    source_status = query_one(
        database_url_schema,
        "SELECT source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )[0]
    assert source_status[0]["errorCategory"] == "unsupported_list"


def test_a_link_heavy_index_page_is_skipped_before_extraction(
    db, discovery, database_url_schema
):
    """URL 不像目录、正文又超过 60 字的入口页，按链接占比在抽取前拦下。

    年度页正文约 500 字、50 条链接、锚文本占八成：不拦就会花掉一次抽取预算，
    抽不出条款时还会落一条 ``extraction_failed`` 伪候选。
    """

    configure(discovery)
    nav_url = "https://www.gov.cn/zhengce/nav/2025n.htm"
    nav_markup = "".join(
        f'<a href="https://www.gov.cn/a/{index}.htm">第{index}号文件</a>'
        for index in range(15)
    )
    nav_text = "政策文件列表" + "".join(f"第{index}号文件" for index in range(15))
    root = page(
        LIST_URL,
        "政策文件列表",
        markup=(
            f'<html><body><a href="{nav_url}">导航</a>'
            f'<a href="{ARTICLE_URL}">措施</a></body></html>'
        ),
    )
    pages = {
        LIST_URL: root,
        nav_url: page(
            nav_url, nav_text, markup=f"<html><body>{nav_markup}</body></html>"
        ),
        ARTICLE_URL: ARTICLE_PAGE,
    }
    llm = FakeLlm()
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=llm).run(trigger="timer")

    assert llm.calls == 1
    assert nav_url not in [row[1] for row in _candidates(database_url_schema)]
    run = query_one(
        database_url_schema,
        "SELECT failed_articles, source_status FROM discovery_run WHERE id = %s",
        (outcome.run_id,),
    )
    assert run[0] == 1
    assert run[1][0]["articleFailures"] == 1


#: 两段足够长的条款句；两站只差导航、页脚，正文逐字相同。
CROSS_SITE_BODY = (
    "一、企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，"
    "再按照实际发生额的100%在税前加计扣除。\n"
    "二、企业享受研发费用加计扣除政策的其他政策口径和管理要求，按照现行规定以及本公告第三条执行。\n"
)


def _cross_site_pages(first_body: str, second_body: str) -> tuple[dict, str, str]:
    """两个站点各转载一份正文；两页导航文字不同，因此整页哈希不同。"""

    first = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    second = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    pages = {
        LIST_URL: page(
            LIST_URL,
            "政策文件列表",
            markup=f'<html><body><a href="{first}">一</a><a href="{second}">二</a></body></html>',
        ),
        first: page(first, "首页 > 政策 > 文件库\n" + first_body + "\n主办单位：国务院办公厅"),
        second: page(second, "税务公报\n" + second_body + "\n网站标识码：bm29000002"),
    }
    return pages, first, second


def test_a_cross_site_copy_is_a_suspected_duplicate_not_a_silent_skip(
    db, discovery, database_url_schema
):
    """换 URL 同正文（DISC-AT-04）：两条候选都在，第二条指向第一条并记疑似重复。

    旧实现按正文指纹命中直接 ``continue``：跨站转载确实只留一条，但「长条款照抄、
    短句不同」的另一份政策也会被同样吞掉（#64 P1）。现在改为保留候选、只提示。
    """

    configure(discovery)
    pages, first, second = _cross_site_pages(CROSS_SITE_BODY, CROSS_SITE_BODY)
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    status, reason, new, duplicates, _failed, _tokens, _late, _finished, suspected = _run_row(
        database_url_schema, outcome.run_id
    )
    assert (status, reason) == ("succeeded", "completed")
    assert (new, duplicates, suspected) == (2, 0, 1)

    rows = query_all(
        database_url_schema,
        "SELECT source_url, suspected_duplicate_of FROM policy_candidate ORDER BY source_url",
    )
    assert len(rows) == 2
    first_id = query_one(
        database_url_schema,
        "SELECT id FROM policy_candidate WHERE source_url = %s",
        (first,),
    )[0]
    assert (rows[0][0], rows[0][1]) == (second, first_id)
    assert (rows[1][0], rows[1][1]) == (first, None)


def test_a_copy_with_a_different_short_clause_is_flagged_not_swallowed(
    db, discovery, database_url_schema
):
    """#64 的复现：长条款逐字相同、只把「适用范围：深圳市」改成「广东省」。

    两句都不带数字，正文指纹相同；旧实现直接跳过，审核员看不到另一份政策。现在两份
    都是候选，后一份带疑似重复提示，由人判断是转载还是适用范围不同的另一份文件。
    """

    configure(discovery)
    pages, first, second = _cross_site_pages(
        CROSS_SITE_BODY + "适用范围：深圳市。",
        CROSS_SITE_BODY + "适用范围：广东省。",
    )
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (2, 0, 1)

    first_id = query_one(
        database_url_schema,
        "SELECT id FROM policy_candidate WHERE source_url = %s",
        (first,),
    )[0]
    flagged = query_one(
        database_url_schema,
        "SELECT suspected_duplicate_of FROM policy_candidate WHERE source_url = %s",
        (second,),
    )
    assert flagged == (first_id,)


def test_a_copy_with_reordered_clauses_is_flagged_not_swallowed(
    db, discovery, database_url_schema
):
    """条款顺序不同也命中正文指纹（句子集合无序）：同样只提示，不跳过。"""

    configure(discovery)
    reordered = "\n".join(reversed(CROSS_SITE_BODY.strip().split("\n")))
    pages, _first, _second = _cross_site_pages(CROSS_SITE_BODY, reordered)
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (2, 0, 1)
    assert query_one(
        database_url_schema,
        "SELECT count(*) FROM policy_candidate WHERE suspected_duplicate_of IS NOT NULL",
    ) == (1,)


#: A → B → A 用例里的三版正文：每版两句长条款，互不相同（指纹与整页哈希都不同）。
POLICY_V1 = (
    "一、企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，"
    "再按照实际发生额的100%在税前加计扣除。\n"
    "二、企业享受研发费用加计扣除政策的其他政策口径和管理要求，按照现行规定以及本公告第三条执行。\n"
)
POLICY_V2 = (
    "一、企业开展研发活动中实际发生的研发费用，形成无形资产的，"
    "按照无形资产成本的200%在税前摊销。\n"
    "二、企业享受研发费用加计扣除政策的其他政策口径和管理要求，按照国家统一规定以及本公告第四条执行。\n"
)
POLICY_V3 = (
    "一、企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，"
    "再按照实际发生额的75%在税前加计扣除。\n"
    "二、企业享受研发费用加计扣除政策的其他政策口径和管理要求，按照本公告口径以及主管税务机关解释执行。\n"
)


def test_a_page_that_reverts_to_an_older_version_is_a_new_version(
    db, discovery, database_url_schema
):
    """同一 URL 走 A → B → A：第三轮仍是版本变化，不能被第一轮的哈希跳过。

    #64 复审 P1：旧实现先查全局历史哈希，第三轮的 A 命中第一轮 A 的指纹表记录，
    被记成确认重复；``versionChanged`` 候选永远出不来。现在「这个 URL 内容变了」先判，
    历史哈希只作为疑似重复的附加提示（指向第一轮那条候选）。
    """

    configure(discovery)
    url = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    list_page = page(
        LIST_URL,
        "政策文件列表",
        markup=f'<html><body><a href="{url}">一</a></body></html>',
    )
    holder = {"now": NOW}

    def _run(body: str, *, days: int):
        holder["now"] = NOW + timedelta(days=days)
        pages = {LIST_URL: list_page, url: page(url, body)}
        return make_runner(
            db, discovery, FakeFetcher(pages), llm=FakeLlm(), holder=holder
        ).run(trigger="timer")

    first = _run(POLICY_V1, days=0)
    second = _run(POLICY_V2, days=1)
    third = _run(POLICY_V1, days=2)

    _s, _r, new2, duplicates2, *_rest2 = _run_row(database_url_schema, second.run_id)
    assert (new2, duplicates2, _rest2[-1]) == (1, 0, 0), "B 是新版本，不是重复"

    _s, _r, new3, duplicates3, *_rest3 = _run_row(database_url_schema, third.run_id)
    assert (new3, duplicates3, _rest3[-1]) == (1, 0, 1), "回退到 A 也要建候选并提示疑似重复"

    rows = query_all(
        database_url_schema,
        "SELECT id, version_changed, duplicate_of, suspected_duplicate_of "
        "FROM policy_candidate ORDER BY created_at, id",
    )
    assert len(rows) == 3
    first_id, second_id, third_id = (row[0] for row in rows)
    assert rows[0][1] is False and rows[0][2] is None
    assert (rows[1][1], rows[1][2]) == (True, first_id)
    assert (rows[2][1], rows[2][2]) == (True, second_id)
    assert rows[2][3] == first_id, "第三轮的内容与第一轮相同，要作为疑似重复提示"


def test_a_changed_page_matching_another_sites_history_is_a_new_version(
    db, discovery, database_url_schema
):
    """同一 URL 的新内容恰好等于另一网址的历史内容：仍按版本变化建候选。

    这是「同 URL 变了」优先于「历史哈希命中」的另一条路径：旧实现会拿别站的指纹
    指纹把这一条记成重复，审核员于是看不到本 URL 的版本变化。
    """

    configure(discovery)
    first_url = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    second_url = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    list_page = page(
        LIST_URL,
        "政策文件列表",
        markup=(
            f'<html><body><a href="{first_url}">一</a><a href="{second_url}">二</a></body></html>'
        ),
    )
    # 第二轮 first_url 的正文与第一轮 second_url 的正文**逐字相同**（整页哈希也相同）。
    shared = "税务公报\n" + POLICY_V3 + "\n网站标识码：bm29000002"
    holder = {"now": NOW}

    def _run(pages: dict, *, days: int):
        holder["now"] = NOW + timedelta(days=days)
        return make_runner(
            db, discovery, FakeFetcher(pages), llm=FakeLlm(), holder=holder
        ).run(trigger="timer")

    _run(
        {
            LIST_URL: list_page,
            first_url: page(
                first_url,
                "首页 > 政策 > 文件库\n" + POLICY_V1 + "\n主办单位：国务院办公厅",
            ),
            second_url: page(second_url, shared),
        },
        days=0,
    )
    outcome = _run(
        {
            LIST_URL: list_page,
            first_url: page(first_url, shared),
            second_url: page(second_url, shared),
        },
        days=1,
    )

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (1, 1, 1), (
        "first_url 变了要建候选；second_url 未变化记确认重复"
    )

    rows = query_all(
        database_url_schema,
        "SELECT id, source_url, version_changed, duplicate_of, suspected_duplicate_of "
        "FROM policy_candidate ORDER BY created_at, source_url",
    )
    # 第一轮两条的 created_at 相同，按 id 排序不稳定，按来源 URL 取。
    old_first = next(row[0] for row in rows if row[1] == first_url and row[2] is False)
    old_second = next(row[0] for row in rows if row[1] == second_url and row[2] is False)
    changed = next(row for row in rows if row[2] is True)
    assert changed[1] == first_url
    assert changed[3] == old_first, "版本变化要指向本 URL 的上一版"
    assert changed[4] == old_second, "与别站历史内容相同只作疑似重复提示"
    assert len({row[0] for row in rows}) == 3
    assert rows[0][2] is False and rows[1][2] is False, "第一轮两条都不是版本变化"


def test_two_recorded_urls_that_change_to_the_same_content_both_keep_changes(
    db, discovery, database_url_schema
):
    """两个已记录网址同一轮变成同一份正文：两条版本变化都要留下。

    #64 复审 P1：``policy_candidate_active_hash`` 只按 ``content_hash`` 唯一时，第一条 C
    落库成功、第二条 C 插入即违反唯一索引，``_store_candidate`` 返回 False 被记成确认
    重复，它那一页的快照也随事务回滚——审核员只看到一个网址的变化。
    """

    configure(discovery)
    first_url = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    second_url = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    list_page = page(
        LIST_URL,
        "政策文件列表",
        markup=(
            f'<html><body><a href="{first_url}">一</a><a href="{second_url}">二</a></body></html>'
        ),
    )
    shared = "政策文件正文\n" + POLICY_V3
    holder = {"now": NOW}

    def _run(pages: dict, *, days: int):
        holder["now"] = NOW + timedelta(days=days)
        return make_runner(
            db, discovery, FakeFetcher(pages), llm=FakeLlm(), holder=holder
        ).run(trigger="timer")

    _run(
        {
            LIST_URL: list_page,
            first_url: page(first_url, POLICY_V1),
            second_url: page(second_url, POLICY_V2),
        },
        days=0,
    )
    outcome = _run(
        {
            LIST_URL: list_page,
            first_url: page(first_url, shared),
            second_url: page(second_url, shared),
        },
        days=1,
    )

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (2, 0, 1), (
        "两条都是版本变化；内容相同只作疑似重复提示，不能少一条"
    )

    rows = query_all(
        database_url_schema,
        "SELECT id, source_url, content_hash, version_changed, duplicate_of "
        "FROM policy_candidate ORDER BY created_at, source_url",
    )
    assert len(rows) == 4
    old = {row[1]: row[0] for row in rows if row[3] is False}
    changed = [row for row in rows if row[3] is True]
    assert {row[1] for row in changed} == {first_url, second_url}
    assert len({row[2] for row in changed}) == 1, "两条新候选内容哈希相同"
    for row in changed:
        assert row[4] == old[row[1]], "版本变化要指向本网址的上一版"

    expected = page(first_url, shared).content_hash
    pages = query_all(
        database_url_schema,
        "SELECT canonical_url, last_content_hash FROM discovery_page ORDER BY canonical_url",
    )
    assert {url: item for url, item in pages}[first_url] == expected
    assert {url: item for url, item in pages}[second_url] == expected


def test_a_new_url_seen_before_a_recorded_url_changes_to_the_same_content(
    db, discovery, database_url_schema
):
    """新网址的 C 先处理、已记录网址随后也变成 C：已记录网址的变化仍要留下。"""

    configure(discovery)
    recorded = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    fresh = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    first_list = page(
        LIST_URL,
        "政策文件列表",
        markup=f'<html><body><a href="{recorded}">一</a></body></html>',
    )
    # 第二轮把新网址放在前面：先落一条 C，再让已记录网址从 A 变到同一份 C。
    second_list = page(
        LIST_URL,
        "政策文件列表",
        markup=f'<html><body><a href="{fresh}">新</a><a href="{recorded}">旧</a></body></html>',
    )
    shared = "政策文件正文\n" + POLICY_V3
    holder = {"now": NOW}

    holder["now"] = NOW
    make_runner(
        db,
        discovery,
        FakeFetcher({LIST_URL: first_list, recorded: page(recorded, POLICY_V1)}),
        llm=FakeLlm(),
        holder=holder,
    ).run(trigger="timer")

    holder["now"] = NOW + timedelta(days=1)
    outcome = make_runner(
        db,
        discovery,
        FakeFetcher(
            {
                LIST_URL: second_list,
                fresh: page(fresh, shared),
                recorded: page(recorded, shared),
            }
        ),
        llm=FakeLlm(),
        holder=holder,
    ).run(trigger="timer")

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (2, 0, 1)

    rows = query_all(
        database_url_schema,
        "SELECT id, source_url, version_changed, duplicate_of FROM policy_candidate "
        "ORDER BY created_at, source_url",
    )
    old = next(row for row in rows if row[1] == recorded and row[2] is False)
    changed = next(row for row in rows if row[1] == recorded and row[2] is True)
    fresh_row = next(row for row in rows if row[1] == fresh)
    assert changed[3] == old[0], "已记录网址的版本变化要指向自己的上一版"
    assert fresh_row[2] is False, "新网址只是一条普通候选"


def test_the_active_hash_index_still_blocks_the_same_url_and_content(
    db, discovery, database_url_schema
):
    """约束收窄到 (content_hash, source_url)：同一网址同内容仍然只允许一条有效候选。"""

    configure(discovery)
    outcome = make_runner(
        db, discovery, FakeFetcher({LIST_URL: LIST_PAGE, ARTICLE_URL: ARTICLE_PAGE}), llm=FakeLlm()
    ).run(trigger="timer")
    assert outcome.reason == "completed"

    run_id, source_url, content_hash = query_one(
        database_url_schema,
        "SELECT run_id, source_url, content_hash FROM policy_candidate",
    )
    sql = (
        "INSERT INTO policy_candidate "
        "(id, run_id, revision, source_url, fetched_at, content_hash, body_text) "
        "VALUES (%s, %s, %s, %s, now(), %s, %s)"
    )
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                sql, (uuid4(), run_id, uuid4(), source_url, content_hash, "正文")
            )
        conn.execute(
            sql,
            (
                uuid4(),
                run_id,
                uuid4(),
                "https://www.chinatax.gov.cn/chinatax/n810765/content_9.html",
                content_hash,
                "正文",
            ),
        )
        count = conn.execute("SELECT count(*) FROM policy_candidate").fetchone()[0]
    assert count == 2, "不同网址允许同内容；同一网址重放被索引挡住"


def test_an_identical_page_is_still_automatically_deduplicated(
    db, discovery, database_url_schema
):
    """整页规范化正文完全相同（含导航）：仍是确认重复，不建第二条候选。"""

    configure(discovery)
    first = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    second = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    same = "政策文件正文\n" + CROSS_SITE_BODY
    pages = {
        LIST_URL: page(
            LIST_URL,
            "政策文件列表",
            markup=f'<html><body><a href="{first}">一</a><a href="{second}">二</a></body></html>',
        ),
        first: page(first, same),
        second: page(second, same),
    }
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    _status, _reason, new, duplicates, *_tail = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates, _tail[-1]) == (1, 1, 0)
    assert len(_candidates(database_url_schema)) == 1


def test_a_changed_page_is_a_new_candidate_marked_version_changed(
    db, discovery, database_url_schema
):
    """同 URL 内容变化：识别成新候选并标版本变化，而不是静默当成重复。"""

    configure(discovery)
    pages, _links = article_pages(1)
    article_url = next(url for url in pages if url != LIST_URL)
    holder = {"now": NOW}
    make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm(), holder=holder).run(
        trigger="timer"
    )
    first_id = query_one(database_url_schema, "SELECT id FROM policy_candidate")[0]

    changed = page(
        article_url,
        BODY + "附表 1\n三、本通知自2026年12月1日起施行，原有关规定同时废止。",
    )
    # 第二轮必须排到计划时间之后，否则会被 ``not_due`` 拦下、根本不写运行记录。
    holder["now"] = NOW + timedelta(days=1)
    second_pages = {LIST_URL: pages[LIST_URL], article_url: changed}
    outcome = make_runner(
        db, discovery, FakeFetcher(second_pages), llm=FakeLlm(), holder=holder
    ).run(trigger="timer")

    status, reason, new, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("succeeded", "completed")
    assert new == 1
    version_changed, duplicate_of = query_one(
        database_url_schema,
        "SELECT version_changed, duplicate_of FROM policy_candidate "
        "ORDER BY created_at DESC LIMIT 1",
    )
    assert version_changed is True
    assert str(duplicate_of) == str(first_id)


def test_a_short_clause_change_on_the_same_url_is_a_new_version(
    db, discovery, database_url_schema
):
    """同一 URL 只改了一个不带数字的短句，指纹完全可以不变——但它仍是新版本。

    指纹只用来认「别的站点转载了同一份公告」；同 URL 的正文变了就是新版政策，
    不能因为长句集合相同就当成重复跳过（M2/M3 在 00c02f1 上复现过）。
    """

    configure(discovery)
    pages, _links = article_pages(1)
    article_url = next(url for url in pages if url != LIST_URL)
    before = page(article_url, BODY + "适用范围为深圳市。")
    holder = {"now": NOW}
    make_runner(
        db,
        discovery,
        FakeFetcher({LIST_URL: pages[LIST_URL], article_url: before}),
        llm=FakeLlm(),
        holder=holder,
    ).run(trigger="timer")
    first_id = query_one(database_url_schema, "SELECT id FROM policy_candidate")[0]

    changed = page(article_url, BODY + "适用范围为广东省。")
    # 前提：这次改动确实不影响指纹，说明新候选不是靠指纹认出来的。
    assert body_fingerprint(changed.text) == body_fingerprint(before.text) is not None

    holder["now"] = NOW + timedelta(days=1)
    outcome = make_runner(
        db,
        discovery,
        FakeFetcher({LIST_URL: pages[LIST_URL], article_url: changed}),
        llm=FakeLlm(),
        holder=holder,
    ).run(trigger="timer")

    status, reason, new, duplicates, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (status, reason) == ("succeeded", "completed")
    assert (new, duplicates) == (1, 0)
    version_changed, duplicate_of = query_one(
        database_url_schema,
        "SELECT version_changed, duplicate_of FROM policy_candidate "
        "ORDER BY created_at DESC LIMIT 1",
    )
    assert version_changed is True
    assert str(duplicate_of) == str(first_id)


def test_a_cross_site_copy_with_a_different_deadline_is_not_merged(
    db, discovery, database_url_schema
):
    """两个站点转载同一份公告，但截止日不同：不是同一份内容，不能合并成一条。"""

    configure(discovery)
    first = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
    second = "https://www.chinatax.gov.cn/chinatax/n810765/content_2.html"
    pages = {
        LIST_URL: page(
            LIST_URL,
            "政策文件列表",
            markup=(
                f'<html><body><a href="{first}">一</a><a href="{second}">二</a></body></html>'
            ),
        ),
        first: page(
            first,
            "首页 > 政策 > 文件库\n"
            + CROSS_SITE_BODY
            + "\n申报截止日期为2026年12月31日。\n主办单位：国务院办公厅",
        ),
        second: page(
            second,
            "税务公报\n"
            + CROSS_SITE_BODY
            + "\n申报截止日期为2027年12月31日。\n网站标识码：bm29000002",
        ),
    }
    outcome = make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    _status, _reason, new, duplicates, *_ = _run_row(database_url_schema, outcome.run_id)
    assert (new, duplicates) == (2, 0)
    assert len(_candidates(database_url_schema)) == 2


def test_an_http_only_attachment_is_still_recorded_on_the_candidate(
    db, discovery, database_url_schema
):
    """只被 http 附件覆盖的页面：附件链接单独留档，不能在候选上显示成「没有附件」。"""

    configure(discovery)
    attachment = "http://www.gov.cn/zhengce/content/2026-11/12/shouce.pdf"
    pages = {
        LIST_URL: LIST_PAGE,
        ARTICLE_URL: page(ARTICLE_URL, BODY, unsupported=(attachment,)),
    }
    make_runner(db, discovery, FakeFetcher(pages), llm=FakeLlm()).run(trigger="timer")

    recorded, unsupported = query_one(
        database_url_schema,
        "SELECT attachment_urls, unsupported_attachment_urls FROM policy_candidate",
    )
    assert recorded == []
    assert unsupported == [attachment]


# ------------------------------------------------------------------ 迁移升级


def test_010_upgrades_a_database_that_ran_an_older_version(database_url_schema):
    """旧版 010 的库：缺列、索引只按 content_hash；重跑 010 必须补齐并改索引。

    ``CREATE TABLE IF NOT EXISTS`` 不会补列，``CREATE UNIQUE INDEX IF NOT EXISTS``
    也不会修改已有索引的定义。迁移里因此显式写了 ALTER 与 DROP INDEX——没有这些
    语句时，任何执行过旧版 010 的库升级后应用查新列都会 500（#64 复审 P1）。
    """

    script = next(path for path in MIGRATIONS if path.name.startswith("010_")).read_text(
        encoding="utf-8"
    )
    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        # 把库退回旧版 010 的形状：列不存在，唯一索引只按 content_hash。
        conn.execute("DROP INDEX IF EXISTS idx_policy_candidate_fingerprint_body")
        conn.execute("DROP INDEX IF EXISTS policy_candidate_active_hash")
        conn.execute(
            "ALTER TABLE policy_candidate DROP COLUMN IF EXISTS suspected_duplicate_of"
        )
        conn.execute(
            "ALTER TABLE policy_candidate DROP COLUMN IF EXISTS unsupported_attachment_urls"
        )
        conn.execute(
            "ALTER TABLE discovery_run DROP COLUMN IF EXISTS suspected_duplicates"
        )
        conn.execute(
            "ALTER TABLE policy_candidate_fingerprint DROP COLUMN IF EXISTS body_fingerprint"
        )
        conn.execute(
            "CREATE UNIQUE INDEX policy_candidate_active_hash "
            "ON policy_candidate (content_hash) "
            "WHERE status IN ('pending_review', 'extraction_failed')"
        )

    try:
        with psycopg.connect(database_url_schema, autocommit=True) as conn:
            conn.execute(script)
    finally:
        # 断言失败也不能把模块 schema 留在旧版；重跑本文件是幂等的。
        with psycopg.connect(database_url_schema, autocommit=True) as conn:
            conn.execute(script)

    columns = {
        row[0]
        for row in query_all(
            database_url_schema,
            "SELECT table_name || '.' || column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name IN "
            "('discovery_run', 'policy_candidate', 'policy_candidate_fingerprint')",
        )
    }
    assert {
        "discovery_run.suspected_duplicates",
        "policy_candidate.unsupported_attachment_urls",
        "policy_candidate.suspected_duplicate_of",
        "policy_candidate_fingerprint.body_fingerprint",
    } <= columns
    definition = query_one(
        database_url_schema,
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND indexname = 'policy_candidate_active_hash'",
    )[0]
    assert "(content_hash, source_url)" in definition
