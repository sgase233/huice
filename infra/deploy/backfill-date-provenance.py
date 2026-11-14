#!/usr/bin/env python3
"""回填 ``policy_version.date_provenance``（VIS-06 的 B1a，E3 #48）。

迁移 ``011_date_provenance.sql`` 只加列；日期来源的实际取值来自 M3 对官方原文的核对
结论，逐条写在 ``date-provenance-backfill.json`` 里，本脚本负责校验、定位政策版本、
把出处绑定到该版本的 ``policy_source`` 行，并幂等写入。

五个设计点：

1. **只认结构，不认业务**：脚本不判断某个日期该是「已核实」还是「推算」，只检查
   数据文件自洽——五个状态之一、``verified`` 必须给出来源与定位、``derived`` 必须
   给出推算依据。口径错了是数据文件的问题，脚本不替它猜。

2. **出处必须绑定到该政策版本**：数据文件写的是来源类型（正文 / **第几份附件，从 1
   起**），脚本在库里查该 ``policy_version_id`` 的 ``policy_source`` 行，把真实 id 写进
   jsonb。这样「能定位」与「定位到的是这个版本的原文」同时成立，评审提的第三条由结构
   保证，而不是靠回填人自觉。

   序号用的是「第几份附件」而不是数据库里的绝对 ``ordinal``：``policy_source`` 的
   ordinal 在版本内连续编号，正文占 0、附件从 1 起，附件数一变绝对号就变。让数据文件
   写绝对号，等于把存储细节漏给写数据的人——写错一次就是一个整批回填失败。

   写入的是旁表 ``policy_version_date_provenance``，**已发布版本一行都不改**：001 的
   ``policy_version_guard_published`` 禁止更新已发布行，把来源塞回 ``policy_version``
   只能靠临时禁用触发器，那正是守卫要防的事。

3. **幂等但绝不静默覆盖**：值相同就不写，连跑两次第二次改动 0 行；值**不同**则停下来
   报冲突，要改写必须显式 ``--overwrite``。回填一次可能被 M3 复核后手工补充过，静默
   整份替换会把复核痕迹抹掉。

4. **默认干跑，且草案状态不许写库**：不加 ``--apply`` 只打印将要改动哪些版本；
   数据文件的 ``reviewStatus`` 不是批准值（``APPLY_REVIEW_STATUSES``）时，``--apply``
   同样拒绝执行。防误写不能只靠「记得别加参数」。

   批准不是一个字符串：``reviewStatus`` 取批准值时必须同时给 ``reviewEvidence``
   （``reviewedBy`` / ``reviewedAt`` / ``reference``——谁在什么时候、依据哪份记录复核的）。
   只写 ``approved-by-m3`` 而不给出处，等于在审计表里放一句没人签过字的话。

5. **干跑要说清楚绑到了哪一行**：每条日期字段解析出的 ``policy_source`` 行都会打印
   文件名、绝对 ``ordinal`` 与字节数，撞到压缩包（``.zip``/``.rar``/``.7z``）另外提示。
   数据文件写的是「第几份附件」，整包导入与逐份导入得到的「第 1 份」不是同一个东西——
   这个风险靠干跑的输出发现，不靠脚本事后猜。

6. **不回显敏感信息**：``DATABASE_URL`` 只从环境读（不上命令行，避免进 ``ps`` 与
   shell 历史）；数据库异常只打印异常类名，不打印连接串、堆栈或 SQL 参数。

用法（仓库根目录，用装了本项目依赖的解释器）::

    DATABASE_URL=... services/api/.venv/Scripts/python.exe infra/deploy/backfill-date-provenance.py
    DATABASE_URL=... services/api/.venv/Scripts/python.exe infra/deploy/backfill-date-provenance.py --apply
    ECS: systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/huice/api.env \\
             -p WorkingDirectory=/opt/huice/app \\
             /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/backfill-date-provenance.py --apply

退出码：0 成功（含干跑）；1 数据库读写失败；2 数据文件、环境不合法或数据未获批准
（只报字段路径与变量名，不报值）；3 与库中已有值冲突且未加 ``--overwrite``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPT_DIR / "date-provenance-backfill.json"

#: 与 ``docs/devlog/M1-11-13-E3方案草案.md`` §4.2 一致，改口径必须同时改草案与契约。
STATUSES = ("verified", "derived", "unverified", "unknown", "not_applicable")
DATE_FIELDS = ("effectiveFrom", "effectiveTo", "applicationWindow")
SOURCE_KINDS = ("body", "attachment")
PROVENANCE_VERSION = 1

#: ``--apply`` 只在这些 ``reviewStatus`` 下执行，且必须同时给 ``reviewEvidence``。
#: 在此之前，仓库里那份 ``draft-pending-m3`` 永远只能干跑。
APPLY_REVIEW_STATUSES = ("approved-by-m3",)

#: 复核证据的三个字段：谁、什么时候、依据哪份记录（PR 评论链接 / 仓库文件 / 提交）。
REVIEW_EVIDENCE_FIELDS = ("reviewedBy", "reviewedAt", "reference")

#: 整包导入的压缩件：绑定到它时，「第几份附件」与包内文件的语义不同，干跑要提示。
ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径，不带连接串或密钥。"""


@dataclass
class Entry:
    """一条回填：按 (source_url, version) 定位政策版本，写入 provenance。"""

    index: int
    source_url: str
    version: str
    provenance: dict[str, Any]
    #: 日期字段 → 来源声明 ``{"kind": "body"}`` / ``{"kind": "attachment", "ordinal": 1}``
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Payload:
    """整份数据文件：批准状态 + 复核证据 + 逐条回填。"""

    review_status: str
    entries: list[Entry]
    #: ``reviewedBy`` / ``reviewedAt`` / ``reference``；草案阶段为 None。
    review_evidence: dict[str, str] | None = None


def _fail(index: int, path: str, reason: str) -> DataError:
    return DataError(f"entries[{index}].{path}: {reason}")


def _require_text(index: int, path: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(index, path, "必须是非空字符串")
    return value.strip()


def _parse_iso_date(index: int, path: str, value: Any) -> None:
    if not isinstance(value, str):
        raise _fail(index, path, "必须是 YYYY-MM-DD 字符串")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise _fail(index, path, "必须是合法的 YYYY-MM-DD 日期") from None


def _validate_review_evidence(raw: Any) -> dict[str, str]:
    """复核证据：谁在什么时候、依据哪份记录签的。缺字段就没法追溯，直接拒绝。"""

    if not isinstance(raw, dict):
        raise DataError("reviewEvidence 必须是对象，含 reviewedBy / reviewedAt / reference")
    extra = set(raw) - set(REVIEW_EVIDENCE_FIELDS)
    if extra:
        raise DataError(f"reviewEvidence 不允许字段 {sorted(extra)}")
    evidence: dict[str, str] = {}
    for name in REVIEW_EVIDENCE_FIELDS:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            raise DataError(f"reviewEvidence.{name} 必须是非空字符串")
        evidence[name] = value.strip()
    try:
        date.fromisoformat(evidence["reviewedAt"])
    except ValueError:
        raise DataError("reviewEvidence.reviewedAt 必须是合法的 YYYY-MM-DD 日期") from None
    return evidence


def _validate_source_spec(index: int, path: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise _fail(index, path, "必须是对象，如 {\"kind\": \"body\"}")
    kind = spec.get("kind")
    if kind not in SOURCE_KINDS:
        raise _fail(index, f"{path}.kind", "只能是 body 或 attachment")
    ordinal = spec.get("ordinal")
    if kind == "body":
        if ordinal is not None:
            raise _fail(index, f"{path}.ordinal", "正文不写序号（每个版本只有一份正文）")
        return {"kind": "body"}
    # 1 起：写的是「第几份附件」，不是数据库里的绝对 ordinal（正文占 0）
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise _fail(index, f"{path}.ordinal", "附件序号从 1 起（第几份附件），不是数据库绝对序号")
    return {"kind": "attachment", "ordinal": ordinal}


def validate_entry(index: int, raw: Any) -> Entry:
    """校验一条回填。返回 Entry；日期字段的来源声明与存储值分开保存。"""
    if not isinstance(raw, dict):
        raise _fail(index, "", "必须是对象")
    source_url = _require_text(index, "sourceUrl", raw.get("sourceUrl"))
    if not source_url.startswith("https://"):
        raise _fail(index, "sourceUrl", "必须是 https 地址（与 policy_version.source_url 的约束一致）")
    version = _require_text(index, "version", raw.get("version"))

    provenance = raw.get("provenance")
    if not isinstance(provenance, dict):
        raise _fail(index, "provenance", "必须是对象")

    unknown_keys = set(provenance) - {"version", "note", *DATE_FIELDS}
    if unknown_keys:
        raise _fail(index, "provenance", f"出现未知字段 {sorted(unknown_keys)}")
    if provenance.get("version") != PROVENANCE_VERSION:
        raise _fail(index, "provenance.version", f"必须是 {PROVENANCE_VERSION}")

    note = provenance.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise _fail(index, "provenance.note", "必须是字符串，或省略")

    sources: dict[str, dict[str, Any]] = {}
    stored: dict[str, Any] = {"version": PROVENANCE_VERSION}

    for name in DATE_FIELDS:
        if name not in provenance:
            continue
        spec = provenance[name]
        path = f"provenance.{name}"
        if not isinstance(spec, dict):
            raise _fail(index, path, "必须是对象")
        status = spec.get("status")
        if status not in STATUSES:
            raise _fail(index, f"{path}.status", f"只能是 {', '.join(STATUSES)}")

        allowed = {"status"}
        if status == "verified":
            allowed |= {"source", "locator"}
            stored[name] = {
                "status": status,
                "locator": _require_text(index, f"{path}.locator", spec.get("locator")),
            }
            sources[name] = _validate_source_spec(index, f"{path}.source", spec.get("source"))
        elif status == "derived":
            allowed |= {"basis", "derivedFrom"}
            derived: dict[str, Any] = {
                "status": status,
                "basis": _require_text(index, f"{path}.basis", spec.get("basis")),
            }
            if spec.get("derivedFrom") is not None:
                _parse_iso_date(index, f"{path}.derivedFrom", spec.get("derivedFrom"))
                derived["derivedFrom"] = spec["derivedFrom"]
            stored[name] = derived
        else:
            stored[name] = {"status": status}

        extra = set(spec) - allowed
        if extra:
            raise _fail(index, path, f"状态 {status} 不允许字段 {sorted(extra)}")

    if note is not None:
        stored["note"] = note.strip()
    if len(stored) == 1:
        raise _fail(index, "provenance", "至少要给一个日期字段的状态或 note")

    return Entry(index=index, source_url=source_url, version=version, provenance=stored, sources=sources)


def load_data(path: Path) -> "Payload":
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"数据文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件不是合法 JSON：{path}（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict):
        raise DataError("数据文件根节点必须是对象")
    if raw.get("version") != 1:
        raise DataError("数据文件 version 必须是 1")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("entries 必须是非空数组")

    review_status = raw.get("reviewStatus")
    if not isinstance(review_status, str) or not review_status.strip():
        raise DataError("reviewStatus 必须是非空字符串（如 draft-pending-m3）")
    evidence = raw.get("reviewEvidence")
    if evidence is not None:
        evidence = _validate_review_evidence(evidence)

    seen: set[tuple[str, str]] = set()
    result: list[Entry] = []
    for index, item in enumerate(entries):
        entry = validate_entry(index, item)
        key = (entry.source_url, entry.version)
        if key in seen:
            raise _fail(index, "sourceUrl+version", "与其他条目重复")
        seen.add(key)
        result.append(entry)
    return Payload(review_status=review_status.strip(), entries=result, review_evidence=evidence)


def _plan_key(entry: Entry) -> tuple[str, str]:
    return (entry.source_url, entry.version)


class OverwriteNotApproved(RuntimeError):
    """覆盖没有命中获准名单，或旧值与干跑时不一致——整批回滚。"""

    def __init__(self, entry: Entry, reason: str) -> None:
        super().__init__(entry.version)
        self.entry = entry
        self.reason = reason


def load_plan(path: Path | None) -> tuple[str, dict[tuple[str, str], dict]]:
    """读回填计划：返回 ``(数据文件摘要, {(sourceUrl, version): 条目})``。

    计划由干跑时的 ``--write-plan`` 生成，固定三样东西：数据文件摘要、每条的
    目标值 ``expectedNew``、以及 ``verified`` 绑定到的来源行（``sourceId`` + 内容
    哈希）；``allowOverwrite`` 只在干跑时该版本**已有不同值**的条目上为真。

    ``--apply`` **必须**带计划：首次插入同样要能证明「写的就是复核过的那份值、
    绑的就是复核过的那一行」，否则干跑与执行之间附件重排或替换，会把未经复核的
    来源写成 ``verified``。
    """

    if path is None:
        raise DataError("--apply 必须同时给 --plan（先干跑生成计划并复核）")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"回填计划不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"回填计划不是合法 JSON（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict) or raw.get("version") != 3:
        raise DataError("回填计划必须是 version 3（请重新干跑生成）")
    data_sha = raw.get("dataFileSha256")
    if not isinstance(data_sha, str) or len(data_sha) != 64:
        raise DataError("回填计划缺少 dataFileSha256")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("回填计划 entries 必须是非空数组")
    plan: dict[tuple[str, str], dict] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DataError(f"回填计划 entries[{index}] 必须是对象")
        for field in ("sourceUrl", "version"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DataError(f"回填计划 entries[{index}].{field} 必须是非空字符串")
        expected_new = item.get("expectedNew")
        if not isinstance(expected_new, dict):
            raise DataError(f"回填计划 entries[{index}].expectedNew 必须是对象")
        sources = item.get("sources")
        if not isinstance(sources, dict):
            raise DataError(f"回填计划 entries[{index}].sources 必须是对象")
        expected_old = item.get("expectedOld")
        if expected_old is not None and not isinstance(expected_old, dict):
            raise DataError(f"回填计划 entries[{index}].expectedOld 必须是对象或 null")
        allow = item.get("allowOverwrite", False)
        if not isinstance(allow, bool):
            raise DataError(f"回填计划 entries[{index}].allowOverwrite 必须是布尔")
        plan[(item["sourceUrl"], item["version"])] = {
            "expectedOld": expected_old,
            "expectedNew": expected_new,
            "sources": sources,
            "allowOverwrite": allow,
        }
    return data_sha, plan


def _check_bindings(entry: "Entry", approved: dict, current: dict) -> None:
    """计划里固定的来源行必须还是同一行：``sourceId`` 与内容哈希都要对得上。"""

    for name, pinned in approved["sources"].items():
        now = current.get(name)
        if now is None:
            raise OverwriteNotApproved(entry, f"{name} 的来源绑定在本次执行里不存在")
        if now.get("sourceId") != pinned.get("sourceId"):
            raise OverwriteNotApproved(entry, f"{name} 绑定的来源行变了（sourceId 不一致）")
        if now.get("contentHash") != pinned.get("contentHash"):
            raise OverwriteNotApproved(entry, f"{name} 绑定的来源内容变了（contentHash 不一致）")

def _human_size(byte_size: int) -> str:
    if byte_size >= 1048576:
        return f"{byte_size / 1048576:.1f} MB"
    return f"{max(byte_size, 1) / 1024:.0f} KB"


class ConcurrentConflict(RuntimeError):
    """检查与写入之间，另一事务改了这一行——停下，不覆盖。"""

    def __init__(self, entry: "Entry") -> None:
        super().__init__(entry.version)
        self.entry = entry


def _redact(message: str) -> str:
    """数据库异常的文案偶尔会带地址；这里只保留类名，绝不回显连接串。"""
    return message.split("://")[0] if "://" in message else message


def review_block(payload: Payload, apply: bool) -> str | None:
    """``--apply`` 前的批准门禁：返回拒绝理由，None 表示允许。干跑一律放行。"""
    if not apply:
        return None
    if payload.review_status not in APPLY_REVIEW_STATUSES:
        return (
            f"数据文件的 reviewStatus 是 {payload.review_status!r}，不是批准值"
            f"（{', '.join(APPLY_REVIEW_STATUSES)}）。M3 复核通过并改掉这个字段之前只允许干跑："
            "未复核的取值写进审计表，事后分不清哪条是人认过的。"
        )
    if payload.review_evidence is None:
        return (
            f"数据文件标了批准值 {payload.review_status!r}，但没有 reviewEvidence。"
            "批准要能追溯：补上 reviewedBy（谁）、reviewedAt（YYYY-MM-DD）、"
            "reference（复核记录位置，如 PR 评论链接或仓库文件），否则审计表里这句话没人签过字。"
        )
    return None


def run(
    data_path: Path,
    apply: bool,
    overwrite: bool = False,
    plan_path: Path | None = None,
    write_plan: Path | None = None,
) -> int:
    payload = load_data(data_path)

    blocked = review_block(payload, apply)
    if blocked is not None:
        print(f"backfill-date-provenance: {blocked}", file=sys.stderr)
        return 2

    if apply and payload.review_evidence is not None:
        evidence = payload.review_evidence
        print(
            f"backfill-date-provenance: 复核证据 {evidence['reviewedBy']} @ "
            f"{evidence['reviewedAt']} — {evidence['reference']}"
        )

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("backfill-date-provenance: 缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2

    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError as exc:
        print(
            f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行"
            "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
            file=sys.stderr,
        )
        return 2

    changed = 0
    skipped = 0
    conflicts: list[tuple[Entry, dict]] = []
    data_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
    try:
        plan_sha, plan = load_plan(plan_path) if apply else (None, None)
    except DataError as exc:
        print(f"backfill-date-provenance: 回填计划不合法：{exc}", file=sys.stderr)
        return 2
    if plan is not None and plan_sha != data_sha:
        print(
            "backfill-date-provenance: 数据文件与覆盖计划生成时不一致（sha256 不同），"
            "整批不写——M3 复核的是计划生成时的那份数据，请重新干跑并重新生成计划。",
            file=sys.stderr,
        )
        return 2
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT (SELECT count(*) FROM policy_version_date_provenance), "
                    "(SELECT count(*) FROM policy_version)"
                )
                before_with, before_total = cur.fetchone()

            # 两阶段：先把每条都对上号（政策版本、来源行），全部成立才开始写。任何一条
            # 对不上都整批停下——回填错位比回填缺失更难发现，写到一半停下更糟。
            resolved: list[tuple[Entry, Any, dict[str, Any]]] = []
            bindings: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
            for entry in payload.entries:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM policy_version WHERE source_url = %s AND version = %s",
                        (entry.source_url, entry.version),
                    )
                    rows = cur.fetchall()
                if len(rows) != 1:
                    print(
                        f"backfill-date-provenance: entries[{entry.index}] 匹配到 {len(rows)} 个政策版本"
                        f"（sourceUrl={entry.source_url} version={entry.version}）。停止：不写任何行。",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                version_id = rows[0][0]

                stored = dict(entry.provenance)
                for name, spec in entry.sources.items():
                    with conn.cursor() as cur:
                        if spec["kind"] == "body":
                            cur.execute(
                                "SELECT id, file_name, ordinal, byte_size, content_hash "
                                "FROM policy_source "
                                "WHERE policy_version_id = %s AND kind = 'body'",
                                (version_id,),
                            )
                        else:
                            # 数据文件给的是「第几份附件」（1 起）；库里 ordinal 在版本内
                            # 连续编号、正文占 0，所以按 ordinal 排序后取第 n 个，不拿绝对号硬匹配。
                            cur.execute(
                                "SELECT id, file_name, ordinal, byte_size, content_hash "
                                "FROM policy_source "
                                "WHERE policy_version_id = %s AND kind = 'attachment' "
                                "ORDER BY ordinal OFFSET %s LIMIT 1",
                                (version_id, spec["ordinal"] - 1),
                            )
                        source_rows = cur.fetchall()
                    if len(source_rows) != 1:
                        print(
                            f"backfill-date-provenance: entries[{entry.index}] 的 {name} 找不到"
                            f"对应的 {spec['kind']}"
                            + (f" 第 {spec['ordinal']} 份" if spec["kind"] == "attachment" else "")
                            + f" 来源（命中 {len(source_rows)} 行）。停止：不写任何行。",
                            file=sys.stderr,
                        )
                        conn.rollback()
                        return 2
                    source_id, file_name, source_ordinal, byte_size, content_hash = source_rows[0]
                    stored[name]["sourceId"] = str(source_id)
                    bindings[entry.index][name] = {
                        "kind": spec["kind"],
                        "sourceId": str(source_id),
                        "fileName": file_name,
                        "contentHash": content_hash,
                    }
                    # 干跑的价值就在这一行：绑到哪个文件、绝对 ordinal 是几，都看得见。
                    where = (
                        "正文"
                        if spec["kind"] == "body"
                        else f"附件第 {spec['ordinal']} 份（policy_source.ordinal={source_ordinal}）"
                    )
                    print(
                        f"entries[{entry.index}] {entry.version} {name} → {where}："
                        f"{file_name}（{_human_size(byte_size)}）"
                    )
                    if spec["kind"] == "attachment" and file_name.lower().endswith(
                        ARCHIVE_SUFFIXES
                    ):
                        print(
                            f"backfill-date-provenance: 注意：entries[{entry.index}] 的 {name} 绑到"
                            f"压缩件 {file_name}。若导入时整包算一份附件，这里绑到的是包而不是包内"
                            "文件——先确认该版本附件是怎么导入的，再决定「第几份」写几。",
                            file=sys.stderr,
                        )
                resolved.append((entry, version_id, stored))

            stored_by_index = {entry.index: stored for entry, _vid, stored in resolved}
            existing_by_index: dict[int, dict | None] = {}
            for entry, version_id, stored in resolved:
                with conn.cursor() as cur:
                    # FOR UPDATE：行存在时锁住它，读到的值与写回时看到的是同一份——
                    # 否则「检查没有冲突」与「写入」之间别人写进来的值会被静默覆盖。
                    cur.execute(
                        "SELECT provenance FROM policy_version_date_provenance "
                        "WHERE policy_version_id = %s FOR UPDATE",
                        (version_id,),
                    )
                    row = cur.fetchone()
                existing_by_index[entry.index] = None if row is None else row[0]

            if write_plan is not None:
                write_plan.write_text(
                    json.dumps(
                        {
                            "version": 3,
                            "dataFileSha256": data_sha,
                            "entries": [
                                {
                                    "sourceUrl": entry.source_url,
                                    "version": entry.version,
                                    "expectedOld": existing_by_index[entry.index],
                                    "expectedNew": stored_by_index[entry.index],
                                    "sources": bindings[entry.index],
                                    # 干跑时已有不同值的条目，才允许在 --overwrite 下改写
                                    "allowOverwrite": existing_by_index[entry.index]
                                    is not None
                                    and existing_by_index[entry.index] != stored_by_index[entry.index],
                                }
                                for entry, _vid, _stored in resolved
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"回填计划已写入 {write_plan}（{len(resolved)} 条，含目标值与附件绑定）。")

            if apply and plan is not None and set(plan) != {_plan_key(entry) for entry, _v, _s in resolved}:
                conn.rollback()
                print(
                    "backfill-date-provenance: 计划与当前数据文件的条目集合不一致，整批不写。",
                    file=sys.stderr,
                )
                return 2

            for entry, version_id, stored in resolved:
                existing = existing_by_index[entry.index]
                with conn.cursor() as cur:
                    approved = plan.get(_plan_key(entry)) if plan is not None else None
                    if apply:
                        # 先校验、再决定跳过还是写入：跳过路径同样要证明「绑定的
                        # 还是复核过的那一行、库里的值与计划生成时一致」。
                        if approved is None:
                            raise OverwriteNotApproved(entry, "不在本次复核过的计划里")
                        if approved["expectedNew"] != stored:
                            raise OverwriteNotApproved(entry, "目标值与复核时的数据文件不一致")
                        _check_bindings(entry, approved, bindings[entry.index])
                        if approved["expectedOld"] != existing:
                            raise OverwriteNotApproved(
                                entry, "库中现值与计划生成时不一致（可能被删除或改写）"
                            )
                    if existing == stored:
                        skipped += 1
                        continue
                    if existing is not None:
                        if not overwrite or not (approved and approved["allowOverwrite"]):
                            conflicts.append((entry, existing))
                            continue
                    if apply:
                        if existing is None:
                            # 没有行：只用「不覆盖」的插入。并发事务若抢先插入，
                            # DO NOTHING 会让本次 rowcount = 0 —— 停下而不是覆盖。
                            cur.execute(
                                "INSERT INTO policy_version_date_provenance "
                                "(policy_version_id, provenance, updated_at) "
                                "VALUES (%s, %s, now()) "
                                "ON CONFLICT (policy_version_id) DO NOTHING",
                                (version_id, Jsonb(stored)),
                            )
                        else:
                            # 覆盖只针对检查时看到的那份值：期间被改过就回滚整批。
                            cur.execute(
                                "UPDATE policy_version_date_provenance "
                                "SET provenance = %s, updated_at = now() "
                                "WHERE policy_version_id = %s AND provenance = %s",
                                (Jsonb(stored), version_id, Jsonb(existing)),
                            )
                        if cur.rowcount != 1:
                            raise ConcurrentConflict(entry)
                    changed += 1
                    print(
                        f"entries[{entry.index}] {'写入' if apply else '将写入'} "
                        f"{entry.version}（{entry.source_url}）"
                    )

            if conflicts:
                conn.rollback()
                names = "、".join(f"entries[{entry.index}] {entry.version}" for entry, _old in conflicts)
                print(
                    f"backfill-date-provenance: {len(conflicts)} 条与库里已有值不同（{names}）。"
                    "默认不覆盖：确认要改写请加 --overwrite，且改写前先确认旧值是否已被复核补充。"
                    "本次未写任何行。",
                    file=sys.stderr,
                )
                return 3
            if apply:
                conn.commit()
            else:
                conn.rollback()

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT (SELECT count(*) FROM policy_version_date_provenance), "
                    "(SELECT count(*) FROM policy_version)"
                )
                after_with, after_total = cur.fetchone()
    except OverwriteNotApproved as exc:
        print(
            f"backfill-date-provenance: entries[{exc.entry.index}] {exc.entry.version} "
            f"{exc.reason}。整批回滚、未写任何行——请重新干跑并重新生成覆盖计划。",
            file=sys.stderr,
        )
        return 3
    except ConcurrentConflict as exc:
        print(
            f"backfill-date-provenance: entries[{exc.entry.index}] {exc.entry.version} 在检查与写入之间"
            "被另一事务改过。整批回滚、未写任何行——请重新干跑，看清当前值再决定是否 --overwrite。",
            file=sys.stderr,
        )
        return 3
    except DataError:
        raise
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"backfill-date-provenance: 数据库操作失败（{_redact(exc.__class__.__name__)}）。", file=sys.stderr)
        return 1

    print(
        f"backfill-date-provenance: {'已写入' if apply else '干跑'} {changed} 行，未变化 {skipped} 行；"
        f"带来源的版本 {before_with}/{before_total} → {after_with}/{after_total}。"
        f"{'' if apply else ' 加 --apply 才会写库。'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填 policy_version.date_provenance（VIS-06 B1a）")
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA,
        help="回填数据文件（默认同目录的 date-provenance-backfill.json）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库；不加时只打印将要改动的条目。数据文件 reviewStatus 未获批准时照样拒绝",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="回填计划（干跑用 --write-plan 生成）；--apply 必须给，逐条固定目标值与附件绑定",
    )
    parser.add_argument(
        "--write-plan",
        type=Path,
        default=None,
        help="干跑时把目标值、附件绑定与「哪些条目允许覆盖」写成计划",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="确认改写计划里标了 allowOverwrite 的条目（默认遇到已有不同值就停）",
    )
    args = parser.parse_args(argv)
    try:
        return run(
            args.data,
            args.apply,
            args.overwrite,
            plan_path=args.plan,
            write_plan=args.write_plan,
        )
    except DataError as exc:
        print(f"backfill-date-provenance: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
