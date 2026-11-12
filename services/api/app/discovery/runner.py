"""一次政策发现的编排：运行锁、批次轮换、抓取、判重、抽取与预算。

# 并发正确性只靠一条连接

E0 D3.1 的结论是「锁与写共用同一条连接」：运行器先在该连接上拿会话级
``pg_try_advisory_lock``，之后**本轮全部数据库写入都走这条连接**。进程崩溃/被杀时
连接断开，PostgreSQL 自动释放锁并回滚未提交事务，因此不存在「锁已经没了、旧任务却
还能提交候选」的窗口——这不是靠事后检查得到的保证，而是机制上做不到。

轮换（旧候选置 ``expired``、插入运行行、推进 ``next_run_at``）就是这个连接上的第一个
事务：要么三件事一起生效，要么都不生效（D3.5）。

# 三个时钟（D3.1）

* **到点判断**：看 ``next_run_at``，不是看「距上次运行多久」——审核员改周期后立刻生效；
* **运行上限**：30 分钟的墙钟，超时中止并记 ``timeout``，与锁的生命周期无关；
* **停机补跑**：到点时若已过期，照跑一次并记 ``late_by_seconds``，不追历史周期。

# 终态可区分（D3.5）

``no_new_policy`` / ``fetch_failed`` / ``partial_failed`` / ``budget_exceeded`` /
``oversize`` / ``timeout`` / ``lock_lost``，都能从 ``discovery_run`` 直接读出来，
页面据此区分「没有新政策」和「抓取失败」，不用猜。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence
from uuid import UUID, uuid4

from psycopg import Connection, OperationalError
from psycopg.errors import UniqueViolation

from ..db import Database
from ..integrations.discovery import DiscoveryRepository
from .extraction import CODE_BODY_EMPTY, CODE_NO_CRITERIA, CandidateExtraction, extract_candidate
from .fetch import (
    MAX_CANDIDATES_PER_RUN,
    MAX_RUN_SECONDS,
    FetchError,
    FetchedPage,
    Fetcher,
    canonicalize_url,
    estimate_tokens,
    extract_links,
)
from .metadata import extract_metadata

logger = logging.getLogger("huice.discovery")

#: 运行锁的键。字符串常量而不是随机值：同一台库上的所有进程必须算出同一个键。
LOCK_SQL = "SELECT pg_try_advisory_lock(hashtext('huice.discovery'))"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('huice.discovery'))"

#: 单次运行 50,000 token、单候选 8,000 token（D3.3 初始值，待 M3 用真实页面校准）。
RUN_TOKEN_BUDGET = 50_000
CANDIDATE_TOKEN_BUDGET = 8_000

#: 单轮最多下载多少个正文页。新候选另有 50 条上限；这个更宽的值用来兜住「反复命中
#: 重复链接」的情形，避免一轮把大量时间花在同一个站点的历史页面上。
MAX_ARTICLE_FETCHES_PER_RUN = 200

#: 抽取本身失败（不是「没抽到条件」）时的内部码，用来决定「本轮还要不要继续调模型」。
_SOFT_EXTRACTION_CODES = frozenset(
    {CODE_NO_CRITERIA, CODE_BODY_EMPTY, "EXTRACTION_BUDGET_EXCEEDED"}
)


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    """CLI 与运维脚本读的结果；不写库，不包含任何正文。"""

    ran: bool
    reason: str
    run_id: UUID | None = None
    new_candidates: int = 0
    duplicates: int = 0
    detail: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.reason in ("completed", "no_new_policy", "disabled", "not_due", "locked")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "runId": str(self.run_id) if self.run_id else None,
            "newCandidates": self.new_candidates,
            "duplicates": self.duplicates,
            "detail": self.detail,
        }


class DiscoveryRunner:
    def __init__(
        self,
        *,
        database: Database,
        repository: DiscoveryRepository,
        llm: Any = None,
        fetcher: Fetcher | None = None,
        model_version: str | None = None,
        now: Callable[[], datetime] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = database
        self._repository = repository
        self._llm = llm
        self._fetcher = fetcher or Fetcher()
        self._model_version = model_version
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._clock = clock

    # ------------------------------------------------------------------ 入口

    def run(self, trigger: str = "cli", *, force: bool = False) -> DiscoveryOutcome:
        """按配置判断是否该跑；该跑就持锁跑完一轮并写运行记录。"""

        settings = self._repository.get_settings()
        if not settings["enabled"]:
            return DiscoveryOutcome(ran=False, reason="disabled", detail="后台未启用自动发现。")

        now = self._now()
        next_run_at = settings["next_run_at"]
        if not force and next_run_at is not None and now < next_run_at:
            return DiscoveryOutcome(
                ran=False,
                reason="not_due",
                detail=f"下一次计划时间为 {next_run_at.isoformat()}。",
            )

        late_by = (
            int((now - next_run_at).total_seconds())
            if next_run_at is not None and now > next_run_at
            else None
        )

        try:
            with self._db.dedicated_connection() as conn:
                if not self._take_lock(conn):
                    return DiscoveryOutcome(
                        ran=False, reason="locked", detail="另一个发现运行正在进行，本轮跳过。"
                    )
                try:
                    return self._run_locked(
                        conn, settings=settings, trigger=trigger, now=now, late_by=late_by
                    )
                finally:
                    self._release_lock(conn)
        except OperationalError as exc:
            # 连专用连接都开不起来：本轮一个字节都没写。
            logger.warning("发现运行无法建立可用的数据库连接：%s", type(exc).__name__)
            return DiscoveryOutcome(ran=False, reason="lock_lost", detail="数据库连接不可用。")

    # ------------------------------------------------------------------ 锁

    def _take_lock(self, conn: Connection) -> bool:
        return bool(conn.execute(LOCK_SQL).fetchone()[0])

    def _release_lock(self, conn: Connection) -> None:
        try:
            conn.execute(UNLOCK_SQL)
        except OperationalError:
            # 连接已经断了：会话级锁随连接一起消失，不需要（也做不到）再解锁。
            logger.debug("运行锁所在连接已断开，锁随会话释放")

    # -------------------------------------------------------------- 主流程

    def _run_locked(
        self,
        conn: Connection,
        *,
        settings: dict[str, Any],
        trigger: str,
        now: datetime,
        late_by: int | None,
    ) -> DiscoveryOutcome:
        run_id = uuid4()
        source_urls = list(settings["source_urls"] or [])
        interval = timedelta(minutes=int(settings["interval_minutes"]))

        with conn.transaction():
            # 本轮持锁，所以仍为 running 的行只可能来自已经死掉的进程（见仓储注释）。
            self._repository.close_stale_runs(conn, now=now)
            # 轮换：旧候选一律过期，即使本轮随后失败也不恢复（D3.5 / §4.1）。
            self._repository.rotate_candidates(conn, now=now)
            self._repository.start_run(
                conn,
                run_id=run_id,
                started_at=now,
                trigger=trigger,
                next_run_at=now + interval,
                late_by_seconds=late_by,
            )

        if not source_urls:
            # 「已启用但没有来源」不是抓取失败，也不是新政策：明确记成一条空轮次，
            # 同时把 next_run_at 推进，避免 timer 每 15 分钟反复重试同一配置错误。
            detail = "尚未配置来源网址，本轮没有抓取。"
            self._finish(
                conn,
                run_id=run_id,
                status="succeeded",
                reason="no_new_policy",
                detail=detail,
                source_status=[],
                counts=_Counts(),
                now=now,
            )
            return DiscoveryOutcome(ran=True, reason="no_new_policy", run_id=run_id, detail=detail)

        started_mono = self._clock()
        counts = _Counts()
        source_status: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        article_fetches = 0
        tokens_used = 0
        tokens_unknown = False
        aborted: str | None = None
        abort_detail: str | None = None
        halt_links = False

        for source_url in source_urls:
            if aborted or halt_links:
                source_status.append(_skipped(source_url, "本轮已提前结束，未检查"))
                continue
            entry: dict[str, Any] = {
                "url": source_url,
                "status": "ok",
                "links": 0,
                "candidates": 0,
                "errorCategory": None,
                "message": None,
            }
            counts.sources_checked += 1
            try:
                page = self._fetcher.fetch(source_url)
            except FetchError as exc:
                entry.update(status="failed", errorCategory=exc.category, message=exc.message)
                counts.failed_sources += 1
                source_status.append(entry)
                continue

            links = extract_links(page.markup or "", page.url) if page.markup else []
            if not links:
                # 单篇正文入口：这一页本身就是候选来源。
                links = [canonicalize_url(page.url) or page.url]
            entry["links"] = len(links)
            counts.links_seen += len(links)

            for link in links:
                if aborted:
                    break
                if article_fetches >= MAX_ARTICLE_FETCHES_PER_RUN:
                    halt_links = True
                    abort_detail = "本轮正文下载达到上限，剩余链接留待下一轮。"
                    break
                if counts.new_candidates >= MAX_CANDIDATES_PER_RUN:
                    halt_links = True
                    abort_detail = (
                        f"本轮新候选已达上限（{MAX_CANDIDATES_PER_RUN} 条），剩余链接留待下一轮。"
                    )
                    break
                if self._clock() - started_mono > MAX_RUN_SECONDS:
                    aborted = "timeout"
                    abort_detail = "本轮超过 30 分钟上限，已中止后续抓取。"
                    break

                article_fetches += 1
                try:
                    article = self._fetcher.fetch(link)
                except FetchError:
                    # 单篇正文抓不到不算来源页失败；链接已计入 links_seen，下一轮会重试。
                    continue

                canonical = canonicalize_url(article.url) or article.url
                content_hash = article.content_hash

                if content_hash in seen_hashes:
                    counts.duplicates += 1
                    continue
                page_row = self._repository.find_page(conn, canonical)
                if page_row is not None and page_row["last_content_hash"] == content_hash:
                    counts.duplicates += 1
                    self._touch_page(conn, canonical, content_hash, page_row["last_candidate_id"], run_id, now)
                    continue
                fingerprint = self._repository.find_fingerprint(conn, content_hash)
                if fingerprint is not None:
                    counts.duplicates += 1
                    self._touch_page(conn, canonical, content_hash, fingerprint["candidate_id"], run_id, now)
                    continue
                seen_hashes.add(content_hash)

                version_changed = page_row is not None
                prior_candidate_id: UUID | None = None
                prior_policy_id: UUID | None = None
                prior_version: str | None = None
                if page_row is not None and page_row["last_candidate_id"]:
                    prior_candidate_id = page_row["last_candidate_id"]
                    prior = self._repository.candidate_policy_ref(conn, prior_candidate_id)
                    if prior is not None:
                        prior_policy_id = prior["submitted_policy_id"]
                        prior_version = prior["submitted_version"]

                candidate_id = uuid4()
                estimate = estimate_tokens(article.text)
                if estimate > CANDIDATE_TOKEN_BUDGET:
                    outcome = _budget_failed(estimate, CANDIDATE_TOKEN_BUDGET)
                elif tokens_used + estimate > RUN_TOKEN_BUDGET:
                    aborted = "budget"
                    abort_detail = "本轮模型预算已用尽，剩余候选留待下一轮。"
                    break
                else:
                    outcome = extract_candidate(
                        candidate_id=candidate_id,
                        source_url=article.url,
                        text=article.text,
                        llm=self._llm,
                        model_version=self._model_version,
                    )
                    tokens_used += outcome.tokens_used
                    if outcome.usage is not None and not outcome.tokens_known:
                        tokens_unknown = True
                        aborted = "budget"
                        abort_detail = (
                            "模型未返回可用的用量，已标记 tokensUnknown 并停止本轮后续抽取。"
                        )

                stored = self._store_candidate(
                    conn,
                    candidate_id=candidate_id,
                    run_id=run_id,
                    now=now,
                    article=article,
                    canonical=canonical,
                    content_hash=content_hash,
                    outcome=outcome,
                    version_changed=version_changed,
                    prior_candidate_id=prior_candidate_id,
                    prior_policy_id=prior_policy_id,
                    prior_version=prior_version,
                )
                if stored:
                    counts.new_candidates += 1
                    entry["candidates"] += 1
                else:
                    counts.duplicates += 1

                if outcome.status != "pending_review":
                    failure = (outcome.extraction or {}).get("failureCode")
                    if failure not in _SOFT_EXTRACTION_CODES and aborted is None:
                        # 模型层面出错：继续调用只会把一次故障放大成几十次。
                        aborted = "model"
                        abort_detail = "抽取调用失败，已停止本轮后续模型调用。"

            source_status.append(entry)

        return self._finish_run(
            conn,
            run_id=run_id,
            now=now,
            counts=counts,
            source_status=source_status,
            aborted=aborted,
            abort_detail=abort_detail,
            tokens_used=tokens_used,
            tokens_unknown=tokens_unknown,
        )

    # ------------------------------------------------------------- 写候选

    def _store_candidate(
        self,
        conn: Connection,
        *,
        candidate_id: UUID,
        run_id: UUID,
        now: datetime,
        article: FetchedPage,
        canonical: str,
        content_hash: str,
        outcome: CandidateExtraction,
        version_changed: bool,
        prior_candidate_id: UUID | None,
        prior_policy_id: UUID | None,
        prior_version: str | None,
    ) -> bool:
        """一个候选一个事务：候选行、指纹、网址快照一起提交，不留半条。"""

        metadata = extract_metadata(article.text, source_url=article.url)
        values: dict[str, Any] = {
            "id": candidate_id,
            "run_id": run_id,
            "revision": uuid4(),
            "status": outcome.status,
            "source_url": article.url,
            "fetched_at": article.fetched_at,
            "content_hash": content_hash,
            "raw_hash": article.raw_hash,
            "storage_key": None,
            "body_text": article.text,
            # 提交目标（新建政策/新版本）由审核员在 PATCH 里决定，抓取器不预设。
            "policy_id": None,
            "version": None,
            **metadata.as_columns(),
            "applicable_entities": None,
            "attachment_urls": json.dumps(list(article.attachments), ensure_ascii=False),
            "missing_fields": json.dumps(metadata.missing_fields(), ensure_ascii=False),
            "criteria": json.dumps(outcome.criteria, ensure_ascii=False),
            "validation_findings": json.dumps(outcome.findings, ensure_ascii=False),
            "extraction": (
                json.dumps(outcome.extraction, ensure_ascii=False)
                if outcome.extraction is not None
                else None
            ),
            "duplicate_of": prior_candidate_id if version_changed else None,
            "existing_policy_id": prior_policy_id if version_changed else None,
            "existing_version": prior_version if version_changed else None,
            "version_changed": version_changed,
            "created_at": now,
            "updated_at": now,
        }
        try:
            with conn.transaction():
                self._repository.insert_candidate(conn, values)
                self._repository.record_fingerprint(
                    conn,
                    content_hash=content_hash,
                    canonical_url=canonical,
                    now=now,
                    outcome="pending",
                    candidate_id=candidate_id,
                )
                self._repository.upsert_page(
                    conn,
                    canonical_url=canonical,
                    content_hash=content_hash,
                    candidate_id=candidate_id,
                    run_id=run_id,
                    now=now,
                )
            return True
        except UniqueViolation:
            # 同一批里另一个链接刚刚写入了同内容：并发去重由部分唯一索引兜底。
            return False

    def _touch_page(
        self,
        conn: Connection,
        canonical_url: str,
        content_hash: str,
        candidate_id: UUID | None,
        run_id: UUID,
        now: datetime,
    ) -> None:
        self._repository.upsert_page(
            conn,
            canonical_url=canonical_url,
            content_hash=content_hash,
            candidate_id=candidate_id,
            run_id=run_id,
            now=now,
        )

    # ------------------------------------------------------------- 收尾

    def _finish_run(
        self,
        conn: Connection,
        *,
        run_id: UUID,
        now: datetime,
        counts: "_Counts",
        source_status: list[dict[str, Any]],
        aborted: str | None,
        abort_detail: str | None,
        tokens_used: int,
        tokens_unknown: bool,
    ) -> DiscoveryOutcome:
        status, reason, detail = _verdict(
            counts=counts,
            aborted=aborted,
            abort_detail=abort_detail,
            source_status=source_status,
        )
        if tokens_unknown and reason == "budget_exceeded":
            suffix = "（模型用量未知，已停止本轮后续消耗。）"
            detail = f"{detail}{suffix}" if detail else suffix
            if abort_detail == "本轮模型预算已用尽，剩余候选留待下一轮。":
                detail = abort_detail + suffix
        try:
            self._finish(
                conn,
                run_id=run_id,
                status=status,
                reason=reason,
                detail=detail,
                source_status=source_status,
                counts=counts,
                now=self._now(),
                tokens_used=tokens_used,
                tokens_unknown=tokens_unknown,
            )
        except OperationalError:
            # 连接在收尾时断开：这条运行行保持 running，下一次运行持锁后会把它收成
            # failed / lock_lost（见 close_stale_runs）。此刻任何写入都进不去。
            logger.warning("发现运行收尾写入失败，运行行留待下次收尾 run=%s", run_id)
        return DiscoveryOutcome(
            ran=True,
            reason=reason,
            run_id=run_id,
            new_candidates=counts.new_candidates,
            duplicates=counts.duplicates,
            detail=detail,
        )

    def _finish(
        self,
        conn: Connection,
        *,
        run_id: UUID,
        status: str,
        reason: str,
        detail: str | None,
        source_status: list[dict[str, Any]],
        counts: "_Counts",
        now: datetime,
        tokens_used: int = 0,
        tokens_unknown: bool = False,
    ) -> None:
        with conn.transaction():
            self._repository.finish_run(
                conn,
                run_id=run_id,
                status=status,
                terminal_reason=reason,
                finished_at=now,
                sources_checked=counts.sources_checked,
                links_seen=counts.links_seen,
                duplicates=counts.duplicates,
                new_candidates=counts.new_candidates,
                failed_sources=counts.failed_sources,
                tokens_used=tokens_used,
                tokens_unknown=tokens_unknown,
                source_status=source_status,
                detail=detail,
            )


@dataclass(slots=True)
class _Counts:
    sources_checked: int = 0
    links_seen: int = 0
    duplicates: int = 0
    new_candidates: int = 0
    failed_sources: int = 0


def _skipped(url: str, message: str) -> dict[str, Any]:
    return {
        "url": url,
        "status": "skipped",
        "links": 0,
        "candidates": 0,
        "errorCategory": None,
        "message": message,
    }


def _verdict(
    *,
    counts: _Counts,
    aborted: str | None,
    abort_detail: str | None,
    source_status: Sequence[dict[str, Any]],
) -> tuple[str, str, str | None]:
    """把本轮的过程事实翻译成契约里的 ``status`` + ``terminalReason``。"""

    if aborted == "timeout":
        return "failed", "timeout", abort_detail
    if aborted == "budget":
        status = "partial_failed" if counts.new_candidates else "failed"
        return status, "budget_exceeded", abort_detail
    if aborted == "model":
        return "partial_failed", "partial_failed", abort_detail

    checked = [item for item in source_status if item["status"] != "skipped"]
    failed = [item for item in checked if item["status"] == "failed"]
    if checked and len(failed) == len(checked):
        categories = {item["errorCategory"] for item in failed}
        reason = next(iter(categories)) if len(categories) == 1 else "fetch_failed"
        if reason not in ("oversize", "timeout"):
            reason = "fetch_failed"
        return "failed", reason, "所有来源都抓取失败。"
    if failed:
        return (
            "partial_failed",
            "partial_failed",
            f"{len(failed)} 个来源抓取失败，其余来源已处理。",
        )
    if counts.new_candidates:
        return "succeeded", "completed", abort_detail
    return "succeeded", "no_new_policy", abort_detail


def _budget_failed(estimate: int, budget: int) -> CandidateExtraction:
    """单候选超预算：不调用模型，候选明确落 ``extraction_failed``。

    运行级预算耗尽才中止整轮；单个页面太大不是运行失败，而是这一条需要人工处理。
    """

    return CandidateExtraction(
        status="extraction_failed",
        extraction={
            "failureCode": "EXTRACTION_BUDGET_EXCEEDED",
            "failureMessage": (
                f"正文估算 {estimate} token，超过单候选 {budget} token 预算，未调用模型。"
            ),
        },
    )
