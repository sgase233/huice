"""发布时写审核事件（D5 的 `reviewsCompleted` 数据源）。

事件必须与发布**同一事务**：发布成功就有事件，失败/回滚不留「已完成」。基线事件
（`review_revision=0`）与人工事件（`review_revision>=1`）靠唯一键区分，不会互相顶掉。
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from app.integrations.storage import CriterionRecord
from db_support import create, query_all

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

GATE = {
    "op": "industry_exclusion",
    "factKey": "industry",
    "period": "assessment",
    "excluded": ["F51", "F52"],
}


def _ready_to_publish(repo, *, version: str = "深工信规〔2025〕4号"):
    """建版本 + 存一条通过校验的条件（发布要求至少一条）。"""

    draft = create(repo, version=version)
    body = draft.sources[0]
    spans = [
        {
            "sourceId": str(body.id),
            "sourceUrl": body.source_url,
            "sourceVersion": version,
            "page": None,
            "section": "合成条款",
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
                expression=GATE,
                spans=spans,
                review_finding="none",
                validation_status="passed",
            )
        ],
        findings=[],
        status="pending_review",
        trace_id=str(uuid4()),
    )
    return draft.id, saved.revision


def _published_with_event(repo, database_url_schema: str, *, actor=None):
    policy_id, revision = _ready_to_publish(repo)
    published = repo.publish(
        policy_id=policy_id,
        version="深工信规〔2025〕4号",
        expected_revision=revision,
        trace_id=str(uuid4()),
        actor_user_id=actor,
    )
    with psycopg.connect(database_url_schema) as conn:
        events = conn.execute(
            "SELECT e.review_revision, e.actor_kind, e.actor_user_id "
            "FROM policy_review_event e WHERE e.policy_version_id = "
            "(SELECT id FROM policy_version WHERE version = '深工信规〔2025〕4号')"
        ).fetchall()
    return published, events


def test_publish_writes_a_user_review_event(database_url_schema: str, repo) -> None:
    actor = uuid4()
    with psycopg.connect(database_url_schema) as conn:
        conn.execute(
            "INSERT INTO app_user (id, username, password_hash, role) "
            "VALUES (%s, 'reviewer.one', %s, 'reviewer')",
            (actor, "x" * 16),
        )
        conn.commit()

    published, events = _published_with_event(repo, database_url_schema, actor=actor)

    assert published.status == "published"
    assert events == [(1, "user", actor)]


def test_a_repeated_publish_does_not_write_a_second_event(
    database_url_schema: str, repo
) -> None:
    policy_id, revision = _ready_to_publish(repo)
    repo.publish(
        policy_id=policy_id,
        version="深工信规〔2025〕4号",
        expected_revision=revision,
        trace_id=str(uuid4()),
        actor_user_id=None,
    )
    with pytest.raises(Exception):
        repo.publish(
            policy_id=policy_id,
            version="深工信规〔2025〕4号",
            expected_revision=revision,
            trace_id=str(uuid4()),
            actor_user_id=None,
        )

    rows = query_all(database_url_schema, "SELECT count(*) FROM policy_review_event")
    assert rows[0][0] == 1


def test_baseline_and_user_events_coexist(database_url_schema: str, repo) -> None:
    """基线用 revision 0、人工发布用 1：同一版本两条事件，唯一键不冲突。"""

    policy_id, revision = _ready_to_publish(repo)
    repo.publish(
        policy_id=policy_id,
        version="深工信规〔2025〕4号",
        expected_revision=revision,
        trace_id=str(uuid4()),
        actor_user_id=None,
    )
    with psycopg.connect(database_url_schema) as conn:
        version_id, policy_id = conn.execute(
            "SELECT id, policy_id FROM policy_version WHERE version = '深工信规〔2025〕4号'"
        ).fetchone()
        conn.execute(
            "INSERT INTO policy_review_event (policy_version_id, policy_id, review_revision, "
            "actor_kind, actor_user_id, occurred_at, legacy_source) "
            "VALUES (%s, %s, 0, 'baseline', NULL, now(), 'reviewed_at')",
            (version_id, policy_id),
        )
        conn.commit()

    rows = query_all(
        database_url_schema,
        "SELECT review_revision FROM policy_review_event ORDER BY review_revision",
    )
    assert [row[0] for row in rows] == [0, 1]
