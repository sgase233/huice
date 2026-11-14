"""E3b 指标查询层（``app/analytics/metrics.py``）的真实数据库用例。

只测「一天怎么算」与「覆盖怎么判」，不涉及路由/契约——端点等 `METRICS_SPEC` 记录口径
后再接。覆盖的关键几条：按指标给状态、按开始时间归日、起点前后与缺口、当天未结算。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.analytics.collection_evidence import host_id
from app.analytics.metrics import MetricsReader, NOT_COLLECTED, PARTIAL, PRECISE
from app.db import Database
from app.domain.versioning import SHANGHAI

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

DAY = date(2026, 11, 20)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def _reader(dsn: str) -> MetricsReader:
    # 这些用例只测日统计；采集证据由 test_collection_evidence 与下面的专项用例覆盖。
    return MetricsReader(Database(dsn), check_journal_evidence=False)


def _version(
    conn,
    *,
    version: str,
    imported_at: datetime,
    published_event_at: datetime | None,
    actor_kind: str = "user",
):
    policy_id, version_id = uuid4(), uuid4()
    conn.execute("INSERT INTO policy (id) VALUES (%s)", (policy_id,))
    conn.execute(
        "INSERT INTO policy_version (id, policy_id, version, title, policy_type, issuer, region, "
        "applicable_entities, published_at, effective_from, source_url, official_entry_url, "
        "imported_at, review_status, revision) VALUES (%s, %s, %s, '合成政策', 'grant', "
        "'深圳市工信局', '440300', %s, '2026-01-01', '2026-01-01', "
        "'https://example.gov.cn/a.html', 'https://example.gov.cn/a.html', %s, "
        "'pending_parse', %s)",
        (version_id, policy_id, version, Jsonb(["企业"]), imported_at, uuid4()),
    )
    if published_event_at is not None:
        if actor_kind == "user":
            reviewer_id = uuid4()
            conn.execute(
                "INSERT INTO app_user (id, username, password_hash, role) "
                "VALUES (%s, %s, 'scrypt$test', 'reviewer')",
                (reviewer_id, f"reviewer.{reviewer_id.hex[:8]}"),
            )
        else:
            reviewer_id = None
        conn.execute(
            "INSERT INTO policy_review_event (policy_version_id, policy_id, review_revision, "
            "actor_kind, actor_user_id, occurred_at, legacy_source) "
            "VALUES (%s, %s, 1, %s, %s, %s, NULL)",
            (version_id, policy_id, actor_kind, reviewer_id, published_event_at),
        )
    return version_id


def _baseline(conn, *, review_from: datetime | None, run_from: datetime | None):
    conn.execute(
        "INSERT INTO collection_baseline (id, review_events_from, review_source, "
        "run_lifecycle_from) VALUES ('default', %s, %s, %s)",
        (review_from, "reviewed_at" if review_from else None, run_from),
    )


def _gap(conn, *, scope: str, start: datetime, end: datetime | None):
    conn.execute(
        "INSERT INTO collection_gap (source, event_id, host, kind, scope, gap_from, gap_to) "
        "VALUES ('host-journal', %s, 'host-a', 'stop', %s, %s, %s)",
        (str(uuid4()), scope, start, end),
    )


def _run(conn, *, created: datetime, status: str, finished: datetime | None):
    enterprise_id, run_id = uuid4(), uuid4()
    conn.execute(
        "INSERT INTO enterprise (id, name_synthetic, period_start, period_end) "
        "VALUES (%s, '合成企业', '2026-01-01', '2026-12-31')",
        (enterprise_id,),
    )
    conn.execute(
        "INSERT INTO assessment_run (id, enterprise_id, as_of, status, fact_version, "
        "model_or_rule_version, created_at, finished_at, period_start, period_end) "
        "VALUES (%s, %s, now(), %s, %s, 'rule:1', %s, %s, '2026-01-01', '2026-12-31')",
        (run_id, enterprise_id, status, "f" * 64, created, finished),
    )


def test_new_drafts_and_reviews_by_day(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 10), run_from=None)
        _version(conn, version="v1", imported_at=_at(DAY, 9), published_event_at=_at(DAY, 11))
        _version(conn, version="v2", imported_at=_at(DAY, 23, 59), published_event_at=None)
        conn.commit()

    rows = _reader(database_url_schema).review_workload(start=DAY, end=DAY, now=_at(DAY, 12))

    assert rows[0]["newDrafts"] == 2
    assert rows[0]["reviewsCompleted"] == 1  # 只算带真实操作者的 user 事件
    assert rows[0]["pendingAtEndOfDay"] is None, "当天未结束应为 null"
    assert rows[0]["dayOpen"] is True
    assert rows[0]["availability"]["newDrafts"] == {"state": PARTIAL, "reason": "DAY_OPEN"}
    assert rows[0]["availability"]["reviewsCompleted"]["state"] == PARTIAL


def test_before_the_review_baseline_is_not_collected(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(date(2026, 11, 21), 10), run_from=None)
        _version(conn, version="v1", imported_at=_at(DAY, 9), published_event_at=None)
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 22), 12)
    )

    assert rows[0]["availability"]["reviewsCompleted"]["state"] == NOT_COLLECTED
    # 待办可以用基线与 imported_at 回放，T_review 之前是 partial，不是未采集。
    assert rows[0]["availability"]["pendingAtEndOfDay"]["state"] == PARTIAL
    assert rows[0]["availability"]["pendingAtEndOfDay"]["reason"] == "BEFORE_COLLECTION"
    assert rows[0]["pendingAtEndOfDay"] == 1


def test_a_gap_downgrades_the_day_and_gives_coverage_from(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=None)
        _gap(conn, scope="review", start=_at(DAY, 9), end=_at(DAY, 11))
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )

    assert rows[0]["availability"]["reviewsCompleted"]["state"] == PARTIAL
    assert rows[0]["availability"]["reviewsCompleted"]["coverageFrom"] == _at(DAY, 11).isoformat()


def test_an_open_gap_has_no_coverage_from(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=None)
        _gap(conn, scope="all", start=_at(DAY, 9), end=None)
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )
    availability = rows[0]["availability"]["reviewsCompleted"]
    assert availability["state"] == PARTIAL
    assert availability["reason"] == "OPEN_GAP"
    assert "coverageFrom" not in availability


def test_run_counts_use_start_day_and_include_interrupted(database_url_schema: str) -> None:
    night = date(2026, 11, 21)
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=None, run_from=_at(DAY, 0))
        _run(conn, created=_at(DAY, 23, 55), status="succeeded", finished=_at(night, 0, 5))
        _run(conn, created=_at(DAY, 10), status="failed", finished=_at(DAY, 10, 1))
        _run(conn, created=_at(DAY, 11), status="interrupted", finished=_at(DAY, 12))
        _run(conn, created=_at(DAY, 12), status="running", finished=None)
        conn.commit()

    rows = _reader(database_url_schema).assessment_runs(
        start=DAY, end=DAY, now=_at(night, 12)
    )
    row = rows[0]

    assert row["total"] == 4  # 跨午夜的运行仍归 11-20
    assert row["succeeded"] == 1
    assert row["failed"] == 1
    assert row["unfinished"] == 2  # interrupted + running
    assert row["durationSampleCount"] == 2  # interrupted 不算耗时样本
    assert row["availability"]["failed"]["state"] == PRECISE


def test_before_the_run_baseline_failed_is_not_collected(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=None, run_from=_at(date(2026, 11, 21), 10))
        _run(conn, created=_at(DAY, 9), status="succeeded", finished=_at(DAY, 9, 1))
        conn.commit()

    rows = _reader(database_url_schema).assessment_runs(
        start=DAY, end=DAY, now=_at(date(2026, 11, 22), 12)
    )
    availability = rows[0]["availability"]

    assert availability["failed"]["state"] == NOT_COLLECTED
    assert availability["total"]["state"] == PARTIAL
    assert availability["total"]["reason"] == "BEFORE_COLLECTION"


def test_legacy_unknown_events_do_not_count_as_completed_reviews(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=None)
        _version(
            conn,
            version="v1",
            imported_at=_at(DAY, 9),
            published_event_at=_at(DAY, 11),
            actor_kind="legacy_unknown",
        )
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )

    assert rows[0]["reviewsCompleted"] == 0, "没有操作者的旧发布不是人工审核完成"


def test_a_gap_does_not_upgrade_not_collected_to_partial(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=None, run_from=None)
        _gap(conn, scope="review", start=_at(DAY, 9), end=None)
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )

    assert rows[0]["availability"]["reviewsCompleted"]["state"] == NOT_COLLECTED
    assert rows[0]["availability"]["pendingAtEndOfDay"]["state"] == NOT_COLLECTED


def test_interrupted_runs_do_not_enter_duration_samples(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=None, run_from=_at(DAY, 0))
        _run(conn, created=_at(DAY, 10), status="interrupted", finished=_at(DAY, 11))
        conn.commit()

    rows = _reader(database_url_schema).assessment_runs(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )

    assert rows[0]["unfinished"] == 1
    assert rows[0]["durationSampleCount"] == 0
    assert rows[0]["avgDurationMs"] is None


def test_missing_journal_evidence_downgrades_precise(
    database_url_schema: str, tmp_path
) -> None:
    empty = tmp_path / "huice"
    empty.mkdir()
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=_at(DAY, 0))
        conn.commit()

    reader = MetricsReader(Database(database_url_schema), journal_dir=empty)
    review = reader.review_workload(start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12))[0]
    runs = reader.assessment_runs(start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12))[0]

    # newDrafts 来自 imported_at，不受主机日志证据影响
    assert review["availability"]["newDrafts"].get("reason") != "HEARTBEAT_GAP_NOT_IMPORTED"
    assert review["availability"]["reviewsCompleted"]["state"] == PARTIAL
    assert review["availability"]["reviewsCompleted"]["reason"] == "NO_JOURNAL"
    assert runs["availability"]["total"]["state"] == PARTIAL
    assert runs["availability"]["total"]["reason"] == "NO_JOURNAL"


def test_pending_is_null_when_baseline_publish_time_is_unknown(
    database_url_schema: str,
) -> None:
    t0 = _at(date(2026, 11, 21), 10)
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=t0, run_from=None)
        version_id = _version(
            conn, version="v1", imported_at=_at(DAY, 9), published_event_at=None
        )
        conn.execute(
            "INSERT INTO policy_review_event (policy_version_id, policy_id, review_revision, "
            "actor_kind, actor_user_id, occurred_at, legacy_source) "
            "SELECT %s, policy_id, 0, 'baseline', NULL, %s, 'unknown' "
            "FROM policy_version WHERE id = %s",
            (version_id, t0, version_id),
        )
        conn.commit()

    rows = _reader(database_url_schema).review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 22), 12)
    )

    assert rows[0]["pendingAtEndOfDay"] is None
    availability = rows[0]["availability"]["pendingAtEndOfDay"]
    assert availability["state"] == NOT_COLLECTED
    assert availability["reason"] == "LEGACY_PUBLISH_TIME_UNKNOWN"


def test_an_unconfigured_journal_still_degrades(database_url_schema: str) -> None:
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=_at(DAY, 0))
        conn.commit()

    reader = MetricsReader(Database(database_url_schema))
    rows = reader.review_workload(start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12))

    assert rows[0]["availability"]["reviewsCompleted"]["state"] == PARTIAL
    assert rows[0]["availability"]["reviewsCompleted"]["reason"] == "NO_JOURNAL"


def _write_journal(directory, events) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"collection-lifecycle-{host_id()}.jsonl"
    lines, prev = [], None
    for index, (kind, at, event_id) in enumerate(events, start=1):
        raw = json.dumps(
            {
                "seq": index,
                "prev": prev,
                "host": host_id(),
                "kind": kind,
                "scope": "all",
                "at": at.isoformat(),
                "event_id": event_id,
                "note": None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(raw)
        prev = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_gap_scope_host_and_source_must_match_the_metric(
    database_url_schema: str, tmp_path
) -> None:
    journal = tmp_path / "huice"
    moments = [
        _at(DAY, 0), _at(DAY, 6), _at(DAY, 12), _at(DAY, 18), _at(date(2026, 11, 21), 0)
    ]
    _write_journal(
        journal,
        [("beat", at, f"b{index}") for index, at in enumerate(moments)],
    )
    with psycopg.connect(database_url_schema) as conn:
        _baseline(conn, review_from=_at(DAY, 0), run_from=_at(DAY, 0))
        for index, (start, end) in enumerate(zip(moments, moments[1:])):
            _gap(conn, scope="review", start=start, end=end)
            conn.execute(
                "UPDATE collection_gap SET event_id = %s, host = %s WHERE gap_from = %s",
                (f"b{index}:silent", host_id(), start),
            )
        conn.commit()

    reader = MetricsReader(Database(database_url_schema), journal_dir=journal)
    review = reader.review_workload(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )[0]
    runs = reader.assessment_runs(
        start=DAY, end=DAY, now=_at(date(2026, 11, 21), 12)
    )[0]

    # review 侧的缺口真实存在，所以那天本来就是 partial；但它的证据不能证明 run 的
    # 心跳空档，run 侧必须因「空档未导入」再降级。
    assert review["availability"]["newDrafts"].get("reason") != "HEARTBEAT_GAP_NOT_IMPORTED"
    assert review["availability"]["reviewsCompleted"]["state"] == PARTIAL
    assert review["availability"]["reviewsCompleted"].get("reason") != "HEARTBEAT_GAP_NOT_IMPORTED"
    assert runs["availability"]["total"]["state"] == PARTIAL
    assert runs["availability"]["total"]["reason"] == "HEARTBEAT_GAP_NOT_IMPORTED"
