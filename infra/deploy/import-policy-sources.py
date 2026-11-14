#!/usr/bin/env python3
"""把「只有版本与来源、还没有条款」的政策写进库（E3 B1a 干跑的前置）。

为什么需要它：`011` 的回填脚本按 `(source_url, version)` 找政策版本，找不到就整批停下。
云端政策库里缺 SMART（深工信规〔2025〕4号）与 GREEN（深工信规〔2024〕12号）两个版本——
它们的申报窗口只写在附件里，`data/policies/sources/` 里没有可导入的正文，所以从没被
导入过。没有这两行，M3 要求的「9 个版本逐条绑定」就缺两条。

**只写版本与来源，不写条款、不发布**：条款内容（`criteria_review.csv`）还没有这两项，
写空条款再发布等于伪造「审核通过」。版本建好后是草稿状态，等 M3 补条款、走正常发布
流程；`policy_version_date_provenance` 挂在版本 id 上，发布后仍然有效。

用法（ECS，venv 解释器）::

    PYTHONPATH=/opt/huice/app/services/api \\
      /opt/huice/venv/bin/python /opt/huice/app/infra/deploy/import-policy-sources.py \\
      --data /opt/huice/app/infra/deploy/policy-source-import.json
    # 确认输出后再加 --apply 写库

退出码：0 成功（含「版本已存在、跳过」）；2 数据文件或环境不合法（只报字段路径与变量名）；
3 写库失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

try:  # noqa: E402
    from app.agents.ingestion import PreparedSource
    from app.db import Database
    from app.integrations.storage import PolicyRepository
    from app.schemas import PolicyImportMetadata
except ModuleNotFoundError as exc:  # noqa: E402
    print(
        f"缺少依赖 {exc.name}：请用项目虚拟环境的解释器运行本脚本"
        "（本地 services/api/.venv，ECS /opt/huice/venv/bin/python）。",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_DATA = SCRIPT_DIR / "policy-source-import.json"


class DataError(ValueError):
    """数据文件不合法。消息里只带字段路径，不带连接串。"""


@dataclass
class SourceSpec:
    kind: str
    path: Path
    file_name: str
    mime_type: str

    def read(self) -> PreparedSource:
        raw = self.path.read_bytes()
        text = raw.decode("utf-8")
        if not text.strip():
            raise DataError(f"{self.path.name} 是空文件")
        return PreparedSource(
            kind=self.kind,
            file_name=self.file_name,
            mime_type=self.mime_type,
            text=text,
            content_hash=hashlib.sha256(raw).hexdigest(),
            byte_size=len(raw) or 1,
        )


@dataclass
class EntrySpec:
    version: str
    title: str
    policy_type: str
    issuer: str
    region: str
    published_at: date
    effective_from: date
    effective_to: date | None
    window: dict[str, str] | None
    source_url: str
    official_entry_url: str
    sources: list[SourceSpec]

    def metadata(self) -> PolicyImportMetadata:
        return PolicyImportMetadata.model_validate(
            {
                "version": self.version,
                "title": self.title,
                "type": self.policy_type,
                "issuer": self.issuer,
                "region": self.region,
                "document_number": self.version,
                "applicable_entities": ["企业"],
                "published_at": self.published_at,
                "effective_from": self.effective_from,
                "effective_to": self.effective_to,
                "application_window": self.window,
                "source_url": self.source_url,
                "official_entry_url": self.official_entry_url,
            }
        )


def _date(raw: object, path: str) -> date:
    if not isinstance(raw, str):
        raise DataError(f"{path} 必须是 YYYY-MM-DD 字符串")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise DataError(f"{path} 必须是合法的 YYYY-MM-DD") from None


def _time(raw: object, path: str) -> time:
    if not isinstance(raw, str):
        raise DataError(f"{path} 必须是 HH:MM 字符串")
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise DataError(f"{path} 必须是合法的 HH:MM") from None


def _window(raw: object, base: Path) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DataError("applicationWindow 必须是对象或 null")
    opens = _date(raw.get("opensAt"), "applicationWindow.opensAt")
    closes = _date(raw.get("closesAt"), "applicationWindow.closesAt")
    opens_at = _time(raw.get("opensTime", "00:00"), "applicationWindow.opensTime")
    closes_at = _time(raw.get("closesTime", "23:59"), "applicationWindow.closesTime")
    return {
        "opensAt": datetime.combine(opens, opens_at, SHANGHAI).isoformat(),
        "closesAt": datetime.combine(closes, closes_at, SHANGHAI).isoformat(),
    }


def _source(raw: object, base: Path) -> SourceSpec:
    if not isinstance(raw, dict):
        raise DataError("sources[] 的每一项必须是对象")
    kind = raw.get("kind")
    if kind not in ("body", "attachment"):
        raise DataError("sources[].kind 只能是 body 或 attachment")
    file_name = raw.get("file")
    if not isinstance(file_name, str) or not file_name.strip():
        raise DataError("sources[].file 必须是非空字符串")
    path = (base / file_name).resolve()
    if not path.is_file():
        raise DataError(f"sources[].file 指向的文件不存在：{path}")
    return SourceSpec(
        kind=kind,
        path=path,
        file_name=str(raw.get("fileName") or path.name),
        mime_type=str(raw.get("mimeType") or "text/plain"),
    )


def load_entries(path: Path) -> list[EntrySpec]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"数据文件不存在：{path}") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"数据文件不是合法 JSON（第 {exc.lineno} 行）") from None
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise DataError("数据文件根节点必须是对象且 version 为 1")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DataError("entries 必须是非空数组")

    base = path.parent
    result: list[EntrySpec] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise DataError(f"entries[{index}] 必须是对象")
        for field in ("version", "title", "type", "issuer", "region", "sourceUrl"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise DataError(f"entries[{index}].{field} 必须是非空字符串")
        key = (item["sourceUrl"], item["version"])
        if key in seen:
            raise DataError(f"entries[{index}] 与其他条目重复")
        seen.add(key)
        sources = [_source(raw_source, base) for raw_source in item.get("sources") or []]
        if not any(source.kind == "body" for source in sources):
            raise DataError(f"entries[{index}].sources 必须含一个 kind=body")
        result.append(
            EntrySpec(
                version=item["version"],
                title=item["title"],
                policy_type=item["type"],
                issuer=item["issuer"],
                region=item["region"],
                published_at=_date(item.get("publishedAt"), f"entries[{index}].publishedAt"),
                effective_from=_date(item.get("effectiveFrom"), f"entries[{index}].effectiveFrom"),
                effective_to=(
                    _date(item["effectiveTo"], f"entries[{index}].effectiveTo")
                    if item.get("effectiveTo")
                    else None
                ),
                window=_window(item.get("applicationWindow"), base),
                source_url=item["sourceUrl"],
                official_entry_url=str(item.get("officialEntryUrl") or item["sourceUrl"]),
                sources=sources,
            )
        )
    return result


def run(data_path: Path, apply: bool) -> int:
    entries = load_entries(data_path)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("缺少环境变量 DATABASE_URL（不打印任何取值）。", file=sys.stderr)
        return 2

    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    database = Database(dsn, max_size=1)
    repository = PolicyRepository(database, cursor_secret="import-policy-sources")
    try:
        for entry in entries:
            with database.connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM policy_version WHERE source_url = %s AND version = %s",
                    (entry.source_url, entry.version),
                ).fetchall()
            if existing:
                print(f"{entry.version}：库里已有该版本（{len(existing)} 行），跳过。")
                continue

            prepared = [source.read() for source in entry.sources]
            if not apply:
                print(
                    f"{entry.version}：将写入正文 {sum(1 for s in prepared if s.kind == 'body')} 条、"
                    f"附件 {sum(1 for s in prepared if s.kind == 'attachment')} 条"
                    + "（" + "、".join(s.file_name for s in prepared) + "）"
                )
                continue

            draft = repository.create_policy_version(
                metadata=entry.metadata(),
                sources=prepared,
                storage_keys=[f"import/{uuid4()}.txt" for _ in prepared],
                trace_id=str(uuid4()),
            )
            print(
                f"{entry.version}：已建版本 {draft.id}（状态 {draft.status}），"
                f"来源 {len(prepared)} 条（第 1 份附件的 ordinal 由导入顺序决定）。"
            )
    except DataError:
        raise
    except Exception as exc:  # 不回显连接串与堆栈
        print(f"写库失败（{type(exc).__name__}）。", file=sys.stderr)
        return 3
    finally:
        database.close()

    print("干跑完成，未写库。" if not apply else "完成。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入只有版本与来源的政策（B1a 回填前置）")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--apply", action="store_true", help="真正写库；不加只打印计划")
    args = parser.parse_args(argv)
    try:
        return run(args.data, args.apply)
    except DataError as exc:
        print(f"import-policy-sources: 数据不合法：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
