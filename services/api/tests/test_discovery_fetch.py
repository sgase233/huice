"""抓取层单测：白名单、逐跳复检、体积/超时/同站间隔、链接与正文解析。

这里一律注入假的 resolver 与 opener，不碰网络：真实 DNS 会让用例随外部状态红绿，
而这些用例要锁的是**拒绝逻辑**——哪个 URL 不该被连、哪种响应该记成哪个失败类别。
"""

from __future__ import annotations

import socket

import pytest

from app.discovery.fetch import (
    MAX_LINKS_PER_PAGE,
    Fetcher,
    FetchError,
    HttpResponse,
    body_fingerprint,
    canonicalize_url,
    estimate_tokens,
    extract_links,
    find_attachments,
    html_to_text,
    looks_like_article,
    normalize_text,
    require_official_url,
    resolve_public_addresses,
)

PUBLIC_IP = "93.184.216.34"


def _opener_returning(status=200, body=b"", headers=None, content_type="text/html; charset=utf-8"):
    """构造一个记录调用并返回固定响应的 opener。"""

    calls: list[tuple] = []

    def opener(host, address, port, target, timeout, max_bytes):
        calls.append((host, address, port, target, timeout, max_bytes))
        merged = {"content-type": content_type}
        merged.update(headers or {})
        return HttpResponse(status=status, headers=merged, body=body, address=address)

    opener.calls = calls  # type: ignore[attr-defined]
    return opener


def _fetcher(**kwargs) -> Fetcher:
    kwargs.setdefault("resolve", lambda host, port: [PUBLIC_IP])
    kwargs.setdefault("sleep", lambda seconds: None)
    return Fetcher(**kwargs)


# ------------------------------------------------------------------ 网址规范


def test_canonicalize_drops_tracking_fragment_and_default_port():
    assert (
        canonicalize_url("HTTPS://WWW.GOV.CN:443/zhengce/tz.html?utm_source=x&id=3#top")
        == "https://www.gov.cn/zhengce/tz.html?id=3"
    )


def test_canonicalize_keeps_path_case():
    """路径大小写有意义：改小写会把两条不同的政策判成同一条。"""

    assert canonicalize_url("https://www.gov.cn/A/b.html") == "https://www.gov.cn/A/b.html"


@pytest.mark.parametrize(
    "raw", ["ftp://www.gov.cn/a.htm", "javascript:void(0)", "https:///nohost", "", "  "]
)
def test_canonicalize_rejects_unusable(raw):
    assert canonicalize_url(raw) is None


# ------------------------------------------------------------------ 静态白名单


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gov.cn/zhengce/",
        "https://www.gov.cn:8443/zhengce/",
        "https://user:pw@www.gov.cn/",
        "https://www.example.com/policy",
        "https://127.0.0.1/",
        "https://localhost/policy",
        "https://192.168.1.10/policy",
    ],
)
def test_require_official_url_rejects(url):
    with pytest.raises(FetchError) as exc:
        require_official_url(url)
    assert exc.value.category == "rejected"


def test_require_official_url_accepts_gov_cn_subdomains():
    require_official_url("https://www.gov.cn/zhengce/zuixin/")
    require_official_url("https://www.sz.gov.cn/zwgk/")
    require_official_url("https://gov.cn/")


# ------------------------------------------------------------------ DNS 校验


def test_resolve_rejects_host_with_any_private_address(monkeypatch):
    """多 A 记录里藏一个内网地址也要整体拒绝——只查首选地址会留个口子。"""

    def fake_getaddrinfo(host, port, type=None):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(FetchError) as exc:
        resolve_public_addresses("www.gov.cn")
    assert exc.value.category == "rejected"


def test_resolve_reports_dns_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(FetchError) as exc:
        resolve_public_addresses("www.gov.cn")
    assert exc.value.category == "dns_failed"


# ------------------------------------------------------------------ 抓取


HTML_PAGE = """
<!doctype html><html><head><title>通知</title><style>p{}</style></head>
<body><h1>关于支持小微企业的通知</h1>
<p>一、减按 5% 计入应纳税所得额。</p>
<a href="/zhengce/content/2026-11/12/content_1.htm">附件说明</a>
<a href="/files/guide.pdf">指南.pdf</a>
<a href="https://example.com/x.htm">外部</a>
<script>var x = 1;</script>
</body></html>
"""


def test_fetch_returns_normalized_text_and_provenance():
    opener = _opener_returning(body=HTML_PAGE.encode("utf-8"))
    fetcher = _fetcher(opener=opener)
    page = fetcher.fetch("https://www.gov.cn/zhengce/zuixin/")

    assert page.url == "https://www.gov.cn/zhengce/zuixin/"
    assert page.status == 200
    assert "减按 5% 计入应纳税所得额" in page.text
    assert "var x = 1" not in page.text, "脚本内容不能进正文"
    assert page.text == normalize_text(page.text)
    assert page.raw.decode("utf-8") == HTML_PAGE
    assert len(page.raw_hash) == 64 and len(page.content_hash) == 64
    assert page.fetched_at.tzinfo is not None
    assert page.markup is not None
    assert any(url.endswith("guide.pdf") for url in page.attachments)
    assert opener.calls[0][0] == "www.gov.cn"
    assert opener.calls[0][1] == PUBLIC_IP
    assert opener.calls[0][3] == "/zhengce/zuixin/"


def test_fetch_follows_redirect_but_rechecks_each_hop():
    """第一跳合法、第二跳跳到非官方域：必须在**连接之前**拒绝。"""

    def opener(host, address, port, target, timeout, max_bytes):
        return HttpResponse(
            status=302,
            headers={"content-type": "text/html", "location": "https://evil.example.com/x"},
            body=b"",
            address=address,
        )

    fetcher = _fetcher(opener=opener)
    with pytest.raises(FetchError) as exc:
        fetcher.fetch("https://www.gov.cn/zhengce/")
    assert exc.value.category == "rejected"


def test_fetch_stops_after_max_redirects():
    def opener(host, address, port, target, timeout, max_bytes):
        return HttpResponse(
            status=301,
            headers={"content-type": "text/html", "location": "/loop"},
            body=b"",
            address=address,
        )

    fetcher = _fetcher(opener=opener, max_redirects=2)
    with pytest.raises(FetchError) as exc:
        fetcher.fetch("https://www.gov.cn/zhengce/")
    assert exc.value.category == "rejected"


def test_fetch_flags_oversize_body():
    opener = _opener_returning(body=b"x" * 64)
    fetcher = _fetcher(opener=opener, max_bytes=32)
    with pytest.raises(FetchError) as exc:
        fetcher.fetch("https://www.gov.cn/zhengce/")
    assert exc.value.category == "oversize"


def test_fetch_flags_http_error_and_timeout():
    fetcher = _fetcher(opener=_opener_returning(status=404))
    with pytest.raises(FetchError) as exc:
        fetcher.fetch("https://www.gov.cn/zhengce/")
    assert exc.value.category == "http_error"

    def timing_out(host, address, port, target, timeout, max_bytes):
        raise socket.timeout("slow")

    with pytest.raises(FetchError) as exc:
        _fetcher(opener=timing_out).fetch("https://www.gov.cn/zhengce/")
    assert exc.value.category == "timeout"


def test_same_host_requests_are_spaced():
    """同站连续请求至少间隔 min_host_interval：靠注入的时钟与 sleep 验证，不真的等。"""

    now = [0.0]
    waits: list[float] = []
    opener = _opener_returning(body=b"<html><body>x</body></html>")

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    fetcher = _fetcher(opener=opener, sleep=sleep, clock=clock, min_host_interval=2.0)
    fetcher.fetch("https://www.gov.cn/a")
    now[0] += 0.5
    fetcher.fetch("https://www.gov.cn/b")

    assert waits == [1.5]


def test_different_hosts_do_not_wait():
    waits: list[float] = []
    opener = _opener_returning(body=b"<html><body>x</body></html>")
    fetcher = _fetcher(opener=opener, sleep=waits.append, min_host_interval=2.0)
    fetcher.fetch("https://www.gov.cn/a")
    fetcher.fetch("https://www.most.gov.cn/b")
    assert waits == []


def test_pdf_response_is_decoded_as_pdf():
    opener = _opener_returning(
        body=b"%PDF-1.4 broken", headers={}, content_type="application/pdf"
    )
    fetcher = _fetcher(opener=opener)
    with pytest.raises(FetchError) as exc:
        fetcher.fetch("https://www.gov.cn/a.pdf")
    # 坏 PDF 不能被当成正文吞掉：要给审核员一个明确失败，而不是空正文
    assert exc.value.category == "unsupported_content"


# ------------------------------------------------------------------ 解析


def test_a_binary_article_fails_visibly_instead_of_becoming_garbage_text():
    """栏目页偶尔直接链到 .docx 或压缩包。

    二进制硬解出来是一屏乱码：既会生成一条谁也看不懂的候选，又要白烧一次模型调用。
    类型不支持必须落成一个可见的失败类别，而不是「抓到了、正文没意义」。
    """

    opener = _opener_returning(
        body=b"PK\x03\x04\x00\x00binary-payload",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )
    with pytest.raises(FetchError) as exc:
        _fetcher(opener=opener).fetch("https://www.gov.cn/zhengce/content/a.docx")
    assert exc.value.category == "unsupported_content"
    assert "内容类型" in exc.value.message


def test_html_under_a_wrong_content_type_is_still_parsed():
    """政府站点常把 HTML 标成 octet-stream：嗅探到 doctype 就照常当正文。"""

    body = "<!doctype html><html><body><p>小微企业优惠政策</p></body></html>".encode()
    opener = _opener_returning(body=body, content_type="application/octet-stream")
    page = _fetcher(opener=opener).fetch("https://www.gov.cn/a")
    assert "小微企业优惠政策" in page.text


def test_plain_text_is_accepted_as_body():
    opener = _opener_returning(
        body="政策正文".encode("utf-8"), content_type="text/plain; charset=utf-8"
    )
    page = _fetcher(opener=opener).fetch("https://www.gov.cn/a")
    assert page.text == "政策正文"


def test_extract_links_keeps_only_official_articles():
    links = extract_links(HTML_PAGE, "https://www.gov.cn/zhengce/zuixin/")
    assert "https://www.gov.cn/zhengce/content/2026-11/12/content_1.htm" in links
    assert "https://example.com/x.htm" not in links
    assert not any(link.endswith(".pdf") for link in links)


def test_extract_links_is_deduplicated_and_capped():
    repeated = "".join(
        f'<a href="/a/{index}.htm">x</a><a href="/a/{index}.htm">x</a>'
        for index in range(MAX_LINKS_PER_PAGE + 20)
    )
    links = extract_links(f"<html><body>{repeated}</body></html>", "https://www.gov.cn/")
    assert len(links) == MAX_LINKS_PER_PAGE
    assert len(set(links)) == len(links)


def test_find_attachments_caps_at_twenty():
    markup = "".join(f'<a href="/f/{i}.pdf">f</a>' for i in range(30))
    found = find_attachments(f"<html><body>{markup}</body></html>", "https://www.gov.cn/")
    assert len(found) == 20


def test_find_attachments_keeps_only_https_links():
    """契约里的 OfficialUrl 只接受 https：页面上的 http 附件地址不能进候选。"""

    markup = (
        '<a href="http://www.gov.cn/f/1.pdf">http</a>'
        '<a href="/f/2.pdf">relative</a>'
    )
    found = find_attachments(f"<html><body>{markup}</body></html>", "https://www.gov.cn/")
    assert found == ("https://www.gov.cn/f/2.pdf",)


#: 两段足够长的条款句：正文指纹至少要能认出两句长句。
LONG_BODY = (
    "一、企业开展研发活动中实际发生的研发费用，未形成无形资产计入当期损益的，再按照实际发生额的100%在税前加计扣除。"
    "\n"
    "二、企业享受研发费用加计扣除政策的其他政策口径和管理要求，按照现行规定以及本公告第三条执行。"
)


def test_cross_site_copies_share_a_body_fingerprint():
    """同一份公告被两个站点转载：整页文本不同，正文长句集合相同。"""

    first = normalize_text("首页 > 政策 > 文件库\n" + LONG_BODY + "\n主办单位：国务院办公厅")
    second = normalize_text("税务公报\n" + LONG_BODY + "\n网站标识码：bm29000002")
    assert body_fingerprint(first) is not None
    assert body_fingerprint(first) == body_fingerprint(second)


def test_a_page_with_one_short_sentence_has_no_body_fingerprint():
    text = normalize_text("关于某政策的通知\n一、符合条件的，给予补助。")
    assert body_fingerprint(text) is None, "句子不够多时退回整页哈希，不硬猜"


def test_only_pages_with_real_text_look_like_articles():
    assert not looks_like_article("")
    assert not looks_like_article("点击跳转")
    assert looks_like_article("政策正文" * 20)


def test_gb18030_page_is_decoded():
    body = "<html><body><p>小微企业优惠政策</p></body></html>".encode("gb18030")
    opener = _opener_returning(body=body, content_type="text/html")
    page = _fetcher(opener=opener).fetch("https://www.gov.cn/a")
    assert "小微企业优惠政策" in page.text


def test_html_to_text_and_normalize():
    assert html_to_text("<div>甲&nbsp;&nbsp; 乙</div><div></div><div>丙</div>") == "甲 乙\n丙"
    assert normalize_text("  a \r\n\r\n\r\n b\t\tc \n") == "a\nb c"


def test_estimate_tokens_is_a_conservative_upper_bound():
    text = "政" * 1500
    assert estimate_tokens(text) == int(1500 / 1.5) + 64
    assert estimate_tokens("") == 64
