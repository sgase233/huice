"""E3b / D5 的只读统计：审核工作量与运行趋势。

口径只写在这一处：两个端点（``GET /admin/metrics/review-workload`` 与
``/admin/metrics/assessment-runs``）只做鉴权与参数边界，日统计与覆盖判定都在这里。

三条与规格对齐的硬规则（M2 本轮逐条点名）：

1. **按指标给覆盖**，不是整行一个状态：`newDrafts` 历来可读；`reviewsCompleted` 在
   `T_review` 之前是 `not_collected`（基线事件不是可追溯的人工审核完成）；
   `pendingAtEndOfDay` 之前按基线回放、标 `partial`。
2. **按开始时间归日**（`Asia/Shanghai`），跨午夜不改归日；耗时按真实起止算。
3. **证据不足就降级**：起点缺失、`collection_gap` 与该指标 scope 重叠、当天未结束，
   都不给 `precise`。`interrupted` 是运行状态，只进 `unfinished`，不直接改覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from ..db import Database
from ..domain.versioning import SHANGHAI

METRIC_RUN = "run"
METRIC_REVIEW = "review"

PRECISE = "precise"
PARTIAL = "partial"
NOT_COLLECTED = "not_collected"

#: 只有带真实操作者的发布才算「人工审核完成」。``baseline`` 是上线基线；
#: ``legacy_unknown`` 是没有账号的旧发布，D5 §2.2/§5 明确不计入完成数。
REVIEW_ACTORS = ("user",)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """``[当日 00:00, 次日 00:00)``（Asia/Shanghai，半开区间）。"""

    start = datetime.combine(day, time.min, tzinfo=SHANGHAI)
    return start, start + timedelta(days=1)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


@dataclass(frozen=True)
class Availability:
    state: str
    coverage_from: datetime | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state}
        if self.coverage_from is not None:
            # 统一按北京时间输出：库里存 UTC，接口里给 +08:00
            payload["coverageFrom"] = self.coverage_from.astimezone(SHANGHAI).isoformat()
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def _from_gaps(base: Availability, day: date, gaps: Iterable[tuple[datetime, datetime | None]]) -> Availability:
    """有缺口压在当天就降级；`coverageFrom` 取最后一个闭合缺口的结束时刻。"""

    start, end = _day_bounds(day)
    overlapping = [
        (gap_from, gap_to)
        for gap_from, gap_to in gaps
        if (gap_to is None and gap_from < end)
        or (gap_to is not None and gap_from < end and gap_to > start)
    ]
    if not overlapping:
        return base
    if any(gap_to is None for _gap_from, gap_to in overlapping):
        return Availability(PARTIAL, None, "OPEN_GAP")
    latest = max(gap_to for _gap_from, gap_to in overlapping if gap_to is not None)
    return Availability(PARTIAL, latest, None)


class MetricsReader:
    """两个指标 + 覆盖状态。只读；鉴权由接端点的一方负责。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ 审核

    def review_workload(
        self, *, start: date, end: date, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        days = _days(start, end)
        with self._db.connection() as conn:
            baseline = self._baseline(conn)
            gaps = self._review_gaps(conn, start, end)
            new_drafts = self._new_drafts(conn, start, end)
            reviews = self._reviews(conn, start, end)
            pending = self._pending(conn, days)

        result = []
        for day in days:
            day_open = day == current.date() and current < _day_bounds(day)[1]
            availability = {
                "newDrafts": self._review_coverage("newDrafts", day, baseline, gaps, day_open),
                "reviewsCompleted": self._review_coverage(
                    "reviewsCompleted", day, baseline, gaps, day_open
                ),
                "pendingAtEndOfDay": self._review_coverage(
                    "pendingAtEndOfDay", day, baseline, gaps, day_open
                ),
            }
            # 缺测不是 0：未采集的指标返回 None，不返回 0（D5 §3.5）。
            reviews_value: int | None = reviews.get(day, 0)
            if availability["reviewsCompleted"].state == NOT_COLLECTED:
                reviews_value = None
            pending_value: int | None = None if day_open else pending.get(day)
            if availability["pendingAtEndOfDay"].state == NOT_COLLECTED:
                pending_value = None
            result.append(
                {
                    "date": day.isoformat(),
                    "newDrafts": new_drafts.get(day, 0),
                    "reviewsCompleted": reviews_value,
                    "pendingAtEndOfDay": pending_value,
                    "availability": {name: item.to_dict() for name, item in availability.items()},
                    "dayOpen": day_open,
                }
            )
        return result

    def _review_coverage(
        self,
        metric: str,
        day: date,
        baseline: dict[str, Any] | None,
        gaps: list[tuple[datetime, datetime | None]],
        day_open: bool,
    ) -> Availability:
        start_from = None if baseline is None else baseline.get("review_events_from")
        if metric == "newDrafts":
            # imported_at 历来可读
            base = Availability(PRECISE, None, None)
        elif start_from is None:
            base = Availability(NOT_COLLECTED, None, "NO_REVIEW_BASELINE")
        elif _day_bounds(day)[1] <= start_from:
            base = Availability(NOT_COLLECTED, None, None)
        elif _day_bounds(day)[0] < start_from:
            base = Availability(PARTIAL, start_from, None)
        elif metric == "pendingAtEndOfDay":
            # 基线回放：T_review 之后的日子才是精确的
            base = Availability(PRECISE, None, None)
        else:
            base = Availability(PRECISE, None, None)
        if day_open and base.state == PRECISE:
            base = Availability(PARTIAL, None, "DAY_OPEN")
        return _from_gaps(base, day, gaps)

    # ------------------------------------------------------------------ 运行

    def assessment_runs(
        self, *, start: date, end: date, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        days = _days(start, end)
        with self._db.connection() as conn:
            baseline = self._baseline(conn)
            gaps = self._run_gaps(conn, start, end)
            rows = self._run_counts(conn, start, end)

        lifecycle_from = None if baseline is None else baseline.get("run_lifecycle_from")
        result = []
        for day in days:
            day_open = day == current.date() and current < _day_bounds(day)[1]
            counts = rows.get(day, {})
            availability = {
                name: _from_gaps(
                    self._run_coverage(name, day, lifecycle_from, day_open), day, gaps
                )
                for name in (
                    "total",
                    "succeeded",
                    "failed",
                    "unfinished",
                    "durationSampleCount",
                    "avgDurationMs",
                )
            }
            # 未采集的三态不给 0；采集前历史只把 total/succeeded 记为 partial。
            values: dict[str, int | None] = {}
            for name in ("total", "succeeded", "failed", "unfinished"):
                values[name] = (
                    None
                    if availability[name].state == NOT_COLLECTED
                    else counts.get(name, 0)
                )
            avg = counts.get("avgDurationMs")
            if availability["avgDurationMs"].state == NOT_COLLECTED:
                avg = None
            result.append(
                {
                    "date": day.isoformat(),
                    **values,
                    "durationSampleCount": counts.get("durationSampleCount", 0),
                    "avgDurationMs": avg,
                    "availability": {name: item.to_dict() for name, item in availability.items()},
                    "dayOpen": day_open,
                }
            )
        return result

    def _run_coverage(
        self, metric: str, day: date, lifecycle_from: datetime | None, day_open: bool
    ) -> Availability:
        if lifecycle_from is None:
            return Availability(NOT_COLLECTED, None, "NO_RUN_BASELINE")
        if _day_bounds(day)[1] <= lifecycle_from:
            # 起点之前：total/succeeded 只能是「已记录数」，失败类完全没采集
            if metric in ("total", "succeeded"):
                return Availability(PARTIAL, None, "BEFORE_COLLECTION")
            return Availability(NOT_COLLECTED, None, None)
        if _day_bounds(day)[0] < lifecycle_from:
            return Availability(PARTIAL, lifecycle_from, None)
        if day_open:
            return Availability(PARTIAL, None, "DAY_OPEN")
        return Availability(PRECISE, None, None)

    # -------------------------------------------------------------- 查询细节

    def _baseline(self, conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT review_events_from, review_source, run_lifecycle_from "
            "FROM collection_baseline WHERE id = 'default'"
        ).fetchone()
        if row is None:
            return None
        return {
            "review_events_from": row[0],
            "review_source": row[1],
            "run_lifecycle_from": row[2],
        }

    def _review_gaps(self, conn, start: date, end: date) -> list[tuple[datetime, datetime | None]]:
        lo, hi = _day_bounds(start)[0], _day_bounds(end)[1]
        rows = conn.execute(
            "SELECT gap_from, gap_to FROM collection_gap "
            "WHERE scope IN ('review', 'all') AND gap_from < %s AND (gap_to IS NULL OR gap_to > %s) "
            "ORDER BY gap_from",
            (hi, lo),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def _run_gaps(self, conn, start: date, end: date) -> list[tuple[datetime, datetime | None]]:
        lo, hi = _day_bounds(start)[0], _day_bounds(end)[1]
        rows = conn.execute(
            "SELECT gap_from, gap_to FROM collection_gap "
            "WHERE scope IN ('run', 'all') AND gap_from < %s AND (gap_to IS NULL OR gap_to > %s) "
            "ORDER BY gap_from",
            (hi, lo),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def _new_drafts(self, conn, start: date, end: date) -> dict[date, int]:
        lo, hi = _day_bounds(start)[0], _day_bounds(end)[1]
        rows = conn.execute(
            "SELECT (imported_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) "
            "FROM policy_version WHERE imported_at >= %s AND imported_at < %s GROUP BY 1",
            (lo, hi),
        ).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def _reviews(self, conn, start: date, end: date) -> dict[date, int]:
        lo, hi = _day_bounds(start)[0], _day_bounds(end)[1]
        rows = conn.execute(
            "SELECT (occurred_at AT TIME ZONE 'Asia/Shanghai')::date AS day, count(*) "
            "FROM policy_review_event "
            "WHERE actor_kind = ANY(%s) AND occurred_at >= %s AND occurred_at < %s GROUP BY 1",
            (list(REVIEW_ACTORS), lo, hi),
        ).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def _pending(self, conn, days: list[date]) -> dict[date, int]:
        """日终待办：当天结束前已导入、且当天结束前没有发布事件的正式版本数。"""

        result: dict[date, int] = {}
        for day in days:
            _start, end = _day_bounds(day)
            row = conn.execute(
                "SELECT count(*) FROM policy_version pv WHERE pv.imported_at < %s AND NOT EXISTS ("
                "  SELECT 1 FROM policy_review_event e WHERE e.policy_version_id = pv.id"
                "  AND e.occurred_at < %s)",
                (end, end),
            ).fetchone()
            result[day] = int(row[0])
        return result

    def _run_counts(self, conn, start: date, end: date) -> dict[date, dict[str, Any]]:
        lo, hi = _day_bounds(start)[0], _day_bounds(end)[1]
        rows = conn.execute(
            """
            SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
                   count(*) AS total,
                   count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE status = 'failed') AS failed,
                   count(*) FILTER (WHERE status IN ('queued', 'running', 'interrupted')) AS unfinished,
                   count(*) FILTER (
                       WHERE finished_at IS NOT NULL AND finished_at > created_at
                   ) AS duration_sample_count,
                   avg(extract(epoch FROM (finished_at - created_at)) * 1000) FILTER (
                       WHERE finished_at IS NOT NULL AND finished_at > created_at
                   ) AS avg_duration_ms
            FROM assessment_run
            WHERE created_at >= %s AND created_at < %s
            GROUP BY 1
            """,
            (lo, hi),
        ).fetchall()
        result: dict[date, dict[str, Any]] = {}
        for row in rows:
            result[row[0]] = {
                "total": int(row[1]),
                "succeeded": int(row[2]),
                "failed": int(row[3]),
                "unfinished": int(row[4]),
                "durationSampleCount": int(row[5]),
                "avgDurationMs": None if row[6] is None else round(float(row[6])),
            }
        return result
