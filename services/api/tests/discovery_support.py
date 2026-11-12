"""政策发现用例的共用替身：假页面、假抓取器、假模型与配置助手。

运行器的库用例与候选接口的 HTTP 用例需要**同一批**页面与同一条抽取结果，否则
「运行器写出来的候选」和「接口读到的候选」各有一套假数据，读写不一致会被两边的
测试一起放过。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.discovery.fetch import FetchedPage, FetchError, normalize_text, sha256_hex
from app.discovery.runner import DiscoveryRunner
from app.errors import ApiProblem
from app.integrations.discovery import DiscoveryRepository
from app.integrations.llm import LlmResult

NOW = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
LIST_URL = "https://www.gov.cn/zhengce/zuixin/"
ARTICLE_URL = "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm"
QUOTE = "年度应纳税所得额不超过 300 万元的，减按 5% 计入应纳税所得额。"
BODY = (
    "关于进一步支持小微企业发展的若干措施\n"
    "国办发〔2026〕12号\n"
    "一、对符合条件的小微企业，" + QUOTE + "\n"
)
THRESHOLD = {
    "op": "compare",
    "factKey": "taxableIncome",
    "period": "assessment",
    "comparator": "lte",
    "value": "3000000.00",
    "unit": "CNY",
}


def page(url: str, text: str, *, markup: str | None = None, attachments=()) -> FetchedPage:
    normalized = normalize_text(text)
    raw = normalized.encode("utf-8")
    return FetchedPage(
        url=url,
        status=200,
        content_type="text/html; charset=utf-8",
        text=normalized,
        raw=raw,
        raw_hash=sha256_hex(raw),
        content_hash=sha256_hex(normalized.encode("utf-8")),
        fetched_at=NOW,
        attachments=tuple(attachments),
        markup=markup,
    )


LIST_PAGE = page(
    LIST_URL,
    "政策文件列表",
    markup=f'<html><body><a href="{ARTICLE_URL}">措施</a></body></html>',
)
ARTICLE_PAGE = page(ARTICLE_URL, BODY)


def article_pages(count: int) -> tuple[dict[str, FetchedPage], list[str]]:
    """一个列表页 + ``count`` 篇文章（正文互不相同，因此不会被判重合并）。"""

    links: list[str] = []
    pages: dict[str, FetchedPage] = {}
    for index in range(count):
        url = f"https://www.gov.cn/zhengce/content/2026-11/12/content_{index}.htm"
        links.append(f'<a href="{url}">第{index}号</a>')
        pages[url] = page(url, f"{BODY}附表 {index}")
    pages[LIST_URL] = page(
        LIST_URL, "政策文件列表", markup=f"<html><body>{''.join(links)}</body></html>"
    )
    return pages, links


class FakeFetcher:
    def __init__(self, pages: dict[str, FetchedPage], errors: dict[str, FetchError] | None = None):
        self.pages = pages
        self.errors = errors or {}
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.pages:
            raise FetchError("http_error", f"未准备 {url} 的响应。")
        return self.pages[url]


class FakeLlm:
    """返回一条带原文引文的条款，用量固定且已知。"""

    mode = "deepseek"

    def __init__(self, quote: str = QUOTE, tokens: int = 30):
        self.quote = quote
        self.tokens = tokens
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LlmResult:
        self.calls += 1
        text = json.dumps(
            [
                {
                    "title": "小微企业减计应纳税所得额",
                    "expression": THRESHOLD,
                    "quote": self.quote,
                    "section": "一",
                }
            ],
            ensure_ascii=False,
        )
        return LlmResult(
            text=text,
            model="deepseek-chat",
            prompt_tokens=100,
            completion_tokens=self.tokens,
            elapsed_ms=200,
        )


class EmptyLlm:
    """模型正常返回但没有条款：候选应显式落 ``extraction_failed``。"""

    mode = "deepseek"

    def complete(self, **_: Any) -> LlmResult:
        return LlmResult(
            text="[]", model="deepseek-chat",
            prompt_tokens=10, completion_tokens=1, elapsed_ms=5,
        )


class FailingLlm:
    """真实模型客户端失败时抛的是 ``ApiProblem``（503），这里照着来。"""

    mode = "deepseek"

    def complete(self, **_: Any) -> LlmResult:
        raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", "模型服务暂时不可用。")


def configure(
    discovery: DiscoveryRepository,
    *,
    enabled: bool = True,
    sources=(LIST_URL,),
    interval: int = 1440,
) -> None:
    """按审核员在后台上会做的方式写配置（带 If-Match 的替换语义）。"""

    row = discovery.get_settings()
    discovery.update_settings(
        expected_revision=row["revision"],
        enabled=enabled,
        source_urls=list(sources),
        interval_minutes=interval,
        time_zone="Asia/Shanghai",
    )


def make_runner(
    database,
    discovery: DiscoveryRepository,
    fetcher: FakeFetcher,
    *,
    llm: Any = None,
    holder: dict[str, datetime] | None = None,
    clock=None,
) -> DiscoveryRunner:
    """``holder`` 让用例在两次运行之间推进「现在」，不用真的等。"""

    holder = holder if holder is not None else {"now": NOW}
    return DiscoveryRunner(
        database=database,
        repository=discovery,
        llm=llm,
        fetcher=fetcher,
        model_version="deepseek-chat",
        now=lambda: holder["now"],
        clock=clock or time.monotonic,
    )
