"""DISC-01—08 的真实数据库用例：审核员会话下的读写、条件写与提交链路。

E2 的退出门槛在 HTTP 层的对应断言：

* **未提交候选不进入政策库**：候选一直待着、政策表就是空的；只有 DISC-07 才写政策；
* **下一轮过期正确**：提交过的候选不被轮转带走（``submitted`` 在下一轮仍是 ``submitted``）；
* **并发提交正确**：``If-Match`` + 行锁决定同一候选只可能提交一次；重复提交给出已提交的
  policyId/version 而不是再建一个版本；同一幂等键重放返回**同一份**草稿；
* **失败与新政策可分辨**：运行记录由 DISC-03 覆盖，这里只验「抽取失败的候选不能提交」。

这里的应用连的是测试 schema（真库），因此能验「候选 → 草稿」整条链路真的写进了
``policy`` / ``policy_version`` / ``policy_criterion``。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import auth as auth_tools
from app.main import create_app
from db_support import query_all, query_one
from discovery_support import (
    EmptyLlm,
    FakeFetcher,
    FakeLlm,
    LIST_URL,
    article_pages,
    configure,
    make_runner,
)

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]

PASSWORD = "reviewer-password-0001"
USERNAME = "reviewer.discovery"

#: 审核员补齐的元数据；值是合成的，不对应任何真实政策。``officialEntryUrl`` 由
#: :func:`_confirm` 换成候选自己的来源页——正常操作里审核员填的就是这一页。
FULL_METADATA = {
    "version": "国办发〔2026〕12号",
    "policyId": None,
    "title": "关于进一步支持小微企业发展的若干措施",
    "type": "grant",
    "issuer": "国务院",
    "region": "CN",
    "documentNumber": "国办发〔2026〕12号",
    "applicableEntities": ["小型微利企业"],
    "publishedAt": "2026-11-10",
    "effectiveFrom": "2026-12-01",
    "effectiveTo": None,
    "applicationWindow": None,
}


@pytest.fixture(autouse=True)
def discovery_settings_row(database_url_schema):
    """``clean_tables`` 会清掉迁移预置的配置行，每个用例先补回来。"""

    with psycopg.connect(database_url_schema, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO discovery_settings (id, revision) "
            "VALUES (1, gen_random_uuid()) ON CONFLICT (id) DO NOTHING"
        )


@pytest.fixture
def app_db(settings, database_url_schema, tmp_path):
    application = create_app(
        replace(
            settings,
            database_url=database_url_schema,
            local_object_dir=str(tmp_path / "objects"),
        )
    )
    yield application
    application.state.db.close()
    application.state.cache.close()


@pytest.fixture
def browser(app_db):
    """不带任何凭证的客户端；下面两个夹具都在它上面叠凭证。"""

    return TestClient(app_db)


@pytest.fixture
def reviewer(app_db, browser):
    """真实注册一个审核员并登录：Cookie、CSRF 与 Origin 都由服务端发出。"""

    app_db.state.accounts.register(
        username=USERNAME,
        password_hash=auth_tools.hash_password(PASSWORD),
        role="reviewer",
        profile=None,
    )
    origin = app_db.state.settings.effective_public_origin
    response = browser.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Origin": origin},
    )
    assert response.status_code == 200, response.text
    return {
        "client": browser,
        "csrf": response.json()["csrfToken"],
        "origin": origin,
    }


def _headers(reviewer, *, revision=None, key=None) -> dict[str, str]:
    headers = {"X-CSRF-Token": reviewer["csrf"], "Origin": reviewer["origin"]}
    if revision is not None:
        headers["If-Match"] = f'"{revision}"'
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return headers


def _etag(response) -> str:
    return response.headers["ETag"].strip('"')


def _seed_run(app_db, *, count: int = 1, llm=None) -> None:
    """跑一轮发现，把 ``count`` 条候选放进暂存区（候选用例自己再去库里取）。"""

    configure(app_db.state.discovery)
    pages, _links = article_pages(count)
    make_runner(
        app_db.state.db,
        app_db.state.discovery,
        FakeFetcher(pages),
        llm=llm if llm is not None else FakeLlm(),
    ).run(trigger="timer")


def _candidates(dsn: str) -> list[dict[str, str]]:
    return [
        {"id": str(row[0]), "revision": str(row[1]), "source_url": row[2]}
        for row in query_all(
            dsn,
            "SELECT id, revision, source_url FROM policy_candidate ORDER BY created_at, id",
        )
    ]


def _detail(reviewer, candidate_id: str) -> tuple[dict, str]:
    response = reviewer["client"].get(f"/api/v1/admin/policy-candidates/{candidate_id}")
    assert response.status_code == 200, response.text
    return response.json(), _etag(response)


def _confirm(reviewer, candidate: dict, revision: str, **overrides):
    metadata = {
        **FULL_METADATA,
        "officialEntryUrl": candidate["source_url"],
        **overrides,
    }
    return reviewer["client"].patch(
        f"/api/v1/admin/policy-candidates/{candidate['id']}",
        headers=_headers(reviewer, revision=revision),
        json={"metadata": metadata},
    )


def _submit(reviewer, candidate_id: str, revision: str, key=None):
    return reviewer["client"].post(
        f"/api/v1/admin/policy-candidates/{candidate_id}/submit",
        headers=_headers(reviewer, revision=revision, key=key or uuid4()),
    )


def _policy_counts(dsn: str) -> tuple[int, int, int]:
    return (
        query_one(dsn, "SELECT count(*) FROM policy")[0],
        query_one(dsn, "SELECT count(*) FROM policy_version")[0],
        query_one(dsn, "SELECT count(*) FROM policy_criterion")[0],
    )


# ------------------------------------------------------------------ 配置


def test_settings_are_readable_by_a_session_and_by_the_ops_bearer(app_db, reviewer, bearer):
    first = reviewer["client"].get("/api/v1/admin/discovery/settings")
    assert first.status_code == 200
    body = first.json()
    assert body["enabled"] is False
    assert body["intervalMinutes"] == 1440
    assert body["sourceUrls"] == []
    assert body["limits"]["minIntervalMinutes"] == 360
    assert body["limits"]["runTimeoutMinutes"] == 30
    assert first.headers["ETag"]

    # E1 之后运维 Bearer 只读：读得到，写不了（写见下一个用例）。
    anonymous = TestClient(app_db, headers=bearer)
    second = anonymous.get("/api/v1/admin/discovery/settings")
    assert second.status_code == 200
    assert second.headers["ETag"] == first.headers["ETag"]


def test_settings_write_requires_a_strong_if_match(reviewer):
    payload = {
        "enabled": False,
        "sourceUrls": [LIST_URL],
        "intervalMinutes": 1440,
        "timeZone": "Asia/Shanghai",
    }
    missing = reviewer["client"].put(
        "/api/v1/admin/discovery/settings", headers=_headers(reviewer), json=payload
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "MISSING_IF_MATCH"

    weak = reviewer["client"].put(
        "/api/v1/admin/discovery/settings",
        headers={**_headers(reviewer), "If-Match": 'W/"x"'},
        json=payload,
    )
    assert weak.status_code == 400
    assert weak.json()["error"]["code"] == "INVALID_IF_MATCH"


def test_settings_reject_a_source_outside_the_official_hosts(reviewer):
    current = reviewer["client"].get("/api/v1/admin/discovery/settings")
    response = reviewer["client"].put(
        "/api/v1/admin/discovery/settings",
        headers=_headers(reviewer, revision=_etag(current)),
        json={
            "enabled": True,
            "sourceUrls": ["https://www.example.com/policy"],
            "intervalMinutes": 1440,
            "timeZone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fieldErrors"][0]["field"] == "sourceUrls[0]"


def test_settings_reject_an_interval_below_the_six_hour_floor(reviewer):
    current = reviewer["client"].get("/api/v1/admin/discovery/settings")
    response = reviewer["client"].put(
        "/api/v1/admin/discovery/settings",
        headers=_headers(reviewer, revision=_etag(current)),
        json={
            "enabled": False,
            "sourceUrls": [LIST_URL],
            "intervalMinutes": 60,
            "timeZone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fieldErrors"][0]["field"] == "intervalMinutes"


def test_settings_write_with_the_ops_bearer_is_forbidden(app_db, reviewer, bearer):
    """Bearer 只读：拿它改配置必须 403，不能悄悄生效。"""

    anonymous = TestClient(app_db, headers={**bearer, "Origin": reviewer["origin"]})
    current = anonymous.get("/api/v1/admin/discovery/settings")
    response = anonymous.put(
        "/api/v1/admin/discovery/settings",
        headers={**bearer, "Origin": reviewer["origin"], "If-Match": current.headers["ETag"]},
        json={
            "enabled": False,
            "sourceUrls": [LIST_URL],
            "intervalMinutes": 1440,
            "timeZone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


# ------------------------------------------------------------------ 候选读写


def test_a_staged_candidate_is_listed_but_not_in_the_policy_library(
    app_db, reviewer, database_url_schema
):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)

    listing = reviewer["client"].get("/api/v1/admin/policy-candidates")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [item["id"] for item in items] == [candidate["id"]]
    assert items[0]["status"] == "pending_review"
    missing = set(items[0]["missingFields"])
    assert missing, "抽取不到的必填项必须如实告诉审核员"
    assert "applicableEntities" in missing, "抓取器不替业务判断适用主体"
    # 已经抽到的字段不能出现在待补清单里：清单与库里的值对不上时，审核员会去补一个
    # 本来就有值的字段，真正缺的那几个反而被淹没。
    assert not {"title", "region", "officialEntryUrl"} & missing
    assert items[0]["title"], "标题能抽出来就要存下来"

    body, etag = _detail(reviewer, candidate["id"])
    assert etag == candidate["revision"]
    assert body["criteria"], "提交前必须有结构化条件"
    assert body["usage"]["modelVersion"] == "deepseek-chat"
    assert body["metadata"]["officialEntryUrl"] == candidate["source_url"]

    # 退出门槛 1：候选只在暂存区。
    assert _policy_counts(database_url_schema) == (0, 0, 0)


def test_patch_requires_if_match_and_rejects_stale_or_unordered_metadata(
    app_db, reviewer, database_url_schema
):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)

    without = reviewer["client"].patch(
        f"/api/v1/admin/policy-candidates/{candidate['id']}",
        headers=_headers(reviewer),
        json={"metadata": {**FULL_METADATA, "officialEntryUrl": candidate["source_url"]}},
    )
    assert without.status_code == 428
    assert without.json()["error"]["code"] == "MISSING_IF_MATCH"

    stale = _confirm(reviewer, candidate, str(uuid4()))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    unordered = _confirm(
        reviewer,
        candidate,
        candidate["revision"],
        publishedAt="2026-12-31",
        effectiveFrom="2026-12-01",
    )
    assert unordered.status_code == 422
    fields = unordered.json()["error"]["details"]["fieldErrors"]
    assert fields[0]["field"] == "effectiveFrom"


def test_patch_confirming_metadata_clears_missing_fields(app_db, reviewer, database_url_schema):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)

    response = _confirm(reviewer, candidate, candidate["revision"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["missingFields"] == []
    assert body["metadata"]["version"] == FULL_METADATA["version"]
    assert body["status"] == "pending_review"
    assert _etag(response) != candidate["revision"], "每次写都要换修订号，旧 ETag 随即失效"


# ------------------------------------------------------------------ 提交


def test_submit_writes_the_draft_and_marks_the_candidate(app_db, reviewer, database_url_schema):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)
    revision = _etag(_confirm(reviewer, candidate, candidate["revision"]))

    response = _submit(reviewer, candidate["id"], revision)
    assert response.status_code == 201, response.text
    draft = response.json()

    assert draft["status"] == "pending_review"
    assert draft["version"] == FULL_METADATA["version"]
    assert draft["type"] == "grant"
    assert draft["sources"][0]["sourceUrl"] == candidate["source_url"]
    assert _etag(response) == draft["revision"]
    assert _policy_counts(database_url_schema) == (1, 1, 1)

    # 候选侧：状态与指向都记下来了；取证字段（正文哈希）没被改写。
    status, submitted_id, submitted_version = query_one(
        database_url_schema,
        "SELECT status, submitted_policy_id, submitted_version FROM policy_candidate",
    )
    assert (status, str(submitted_id), submitted_version) == (
        "submitted",
        draft["id"],
        draft["version"],
    )


def test_resubmitting_with_the_same_key_replays_the_same_draft(
    app_db, reviewer, database_url_schema
):
    """幂等键重放：不再建版本，直接返回首次那份草稿。"""

    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)
    revision = _etag(_confirm(reviewer, candidate, candidate["revision"]))

    key = uuid4()
    first = _submit(reviewer, candidate["id"], revision, key=key)
    second = _submit(reviewer, candidate["id"], revision, key=key)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["revision"] == second.json()["revision"]
    assert _policy_counts(database_url_schema) == (1, 1, 1)


def test_submitting_an_already_submitted_candidate_is_a_state_conflict(
    app_db, reviewer, database_url_schema
):
    """刷新后再次提交：不新建版本，而是给出已提交的 policyId/version。"""

    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)
    revision = _etag(_confirm(reviewer, candidate, candidate["revision"]))
    first = _submit(reviewer, candidate["id"], revision)
    assert first.status_code == 201

    _body, refreshed = _detail(reviewer, candidate["id"])
    second = _submit(reviewer, candidate["id"], refreshed)

    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "CANDIDATE_STATE_CONFLICT"
    assert error["details"]["currentStatus"] == "submitted"
    assert error["details"]["submittedPolicyId"] == first.json()["id"]
    assert error["details"]["submittedVersion"] == first.json()["version"]
    assert _policy_counts(database_url_schema) == (1, 1, 1)


def test_the_same_key_on_another_candidate_is_rejected(app_db, reviewer, database_url_schema):
    _seed_run(app_db, count=2)
    candidates = _candidates(database_url_schema)
    assert len(candidates) == 2
    revisions = [_etag(_confirm(reviewer, item, item["revision"])) for item in candidates]

    key = uuid4()
    assert _submit(reviewer, candidates[0]["id"], revisions[0], key=key).status_code == 201
    reused = _submit(reviewer, candidates[1]["id"], revisions[1], key=key)

    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert _policy_counts(database_url_schema) == (1, 1, 1)


def test_submit_refuses_candidates_with_unconfirmed_metadata(
    app_db, reviewer, database_url_schema
):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)

    response = _submit(reviewer, candidate["id"], candidate["revision"])
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    fields = {item["field"] for item in error["details"]["fieldErrors"]}
    assert {"version", "applicableEntities"} <= fields
    assert _policy_counts(database_url_schema) == (0, 0, 0)


def test_submit_refuses_a_candidate_whose_extraction_failed(
    app_db, reviewer, database_url_schema
):
    _seed_run(app_db, llm=EmptyLlm())
    [candidate] = _candidates(database_url_schema)

    response = _submit(reviewer, candidate["id"], candidate["revision"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANDIDATE_STATE_CONFLICT"
    assert response.json()["error"]["details"]["currentStatus"] == "extraction_failed"
    assert _policy_counts(database_url_schema) == (0, 0, 0)


def test_submit_requires_an_idempotency_key(app_db, reviewer, database_url_schema):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)
    revision = _etag(_confirm(reviewer, candidate, candidate["revision"]))

    response = reviewer["client"].post(
        f"/api/v1/admin/policy-candidates/{candidate['id']}/submit",
        headers=_headers(reviewer, revision=revision),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
    assert _policy_counts(database_url_schema) == (0, 0, 0)


# ------------------------------------------------------------------ 丢弃与轮换


def test_discard_is_terminal(app_db, reviewer, database_url_schema):
    _seed_run(app_db)
    [candidate] = _candidates(database_url_schema)

    discarded = reviewer["client"].post(
        f"/api/v1/admin/policy-candidates/{candidate['id']}/discard",
        headers=_headers(reviewer, revision=candidate["revision"]),
        json={"reason": "与既有政策重复"},
    )
    assert discarded.status_code == 200, discarded.text
    body = discarded.json()
    assert body["status"] == "discarded"
    assert body["discardReason"] == "与既有政策重复"

    again = reviewer["client"].post(
        f"/api/v1/admin/policy-candidates/{candidate['id']}/discard",
        headers=_headers(reviewer, revision=_etag(discarded)),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CANDIDATE_STATE_CONFLICT"
    assert _policy_counts(database_url_schema) == (0, 0, 0)


def test_the_next_rotation_expires_unsubmitted_but_keeps_submitted(
    app_db, reviewer, database_url_schema
):
    """退出门槛 2：已提交的候选不会因为下一轮定时任务而消失。"""

    _seed_run(app_db, count=2)
    submitted, pending = _candidates(database_url_schema)
    revision = _etag(_confirm(reviewer, submitted, submitted["revision"]))
    assert _submit(reviewer, submitted["id"], revision).status_code == 201

    # 第二轮抓同一批页面（全部判重）；轮换在抓取之前，过期只针对未提交的候选。
    pages, _links = article_pages(2)
    make_runner(
        app_db.state.db,
        app_db.state.discovery,
        FakeFetcher(pages),
        llm=FakeLlm(),
    ).run(trigger="timer", force=True)

    statuses = {
        str(row[0]): row[1]
        for row in query_all(
            database_url_schema, "SELECT id, status FROM policy_candidate"
        )
    }
    assert statuses[submitted["id"]] == "submitted"
    assert statuses[pending["id"]] == "expired"
    assert _policy_counts(database_url_schema) == (1, 1, 1)
