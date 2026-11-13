"""外网抓取与正文抽取：把「能抓哪里、抓多大、抓多久」写死在一个模块里。

# 为什么是自己写而不是直接用 httpx

判定顺序里有一条是设计文档明确要求的：**每次重定向与最终解析 IP 都要复检**，
禁止回环、内网、链路本地与 DNS 重绑定（EXTENSION_INTERFACES §4.3.2、E0 D3.2）。
httpx 自己解析域名、自己跟重定向，我们拿不到「这次实际连了哪个地址」，
也就无法在连接前把它挡掉。这里用标准库做三件事：

1. **先解析再校验**：每次请求都把主机解析成地址，全部必须是公网地址；
2. **连到校验过的地址，但证书仍按主机名验证**：``SSLContext.wrap_socket`` 的
   ``server_hostname`` 取主机名，``socket.create_connection`` 取解析后的 IP——
   证书校验不会被削弱，「解析结果被换掉」也在连接前就拦住（http.client 默认实现
   是「按主机名再解析一次」，那正是 DNS 重绑定的窗口）；
3. **重定向自己走**：每一跳都重新过一遍 1、2 与白名单，最多 5 跳。

# 残余风险（写进契约，不藏着）

同一台主机在「我们解析」与「我们连接」之间仍可能被投毒成另一个**公网**地址
（只是不再是内网跳板）。首版接受该风险：出网由 nginx/安全组再收一层，
且会话里只抓 ``gov.cn`` 白名单。真正需要连接级约束时，应改用固定出口代理，
而不是在这里加更多字符串检查。

# 不做什么

不执行正文里的任何指令（正文只是数据）、不跟随 JS 跳转、也不保存 Cookie。
**附件一律不下载、不解析**，只在候选上记录 https 链接留给审核员人工核对；只有把
PDF 地址本身配置成正文来源（或页面正文直接就是 PDF）时，才按 PDF 抽取文字。
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from typing import Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

#: 首版白名单：与 ``agents/ingestion.py`` 的官方来源范围一致（#47 首版决定）。
OFFICIAL_HOST_SUFFIXES = ("gov.cn",)

#: 单次运行最多处理多少条新候选（D3.2）。
MAX_CANDIDATES_PER_RUN = 50
#: 单次运行上限：超过即中止本轮并记 ``timeout``（D3.1）。
MAX_RUN_SECONDS = 30 * 60
#: 单响应 2 MiB（D3.2）。压缩包类附件只记链接、不下载。
MAX_BODY_BYTES = 2 * 1024 * 1024
#: 单请求 15 秒（D3.2）。
REQUEST_TIMEOUT_SECONDS = 15.0
#: 同一站点两次请求的最小间隔（D3.2「同站间隔 ≥2s」）。
MIN_HOST_INTERVAL_SECONDS = 2.0
#: 重定向最多 5 跳，每跳复检白名单与地址（D3.2）。
MAX_REDIRECTS = 5
#: 一页最多取多少个链接，防止「下一页/归档」那类页面把一轮抓成几千条。
MAX_LINKS_PER_PAGE = 200

USER_AGENT = "huice-discovery/1.0 (+policy-discovery; https://github.com/sgase233/huice)"

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ATTACHMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z")

#: 只保留页面上「看起来是一条政策正文」的链接：去掉导航、分页、锚点与附件直链。
_NON_ARTICLE_HINTS = (
    "/index",
    "/list",
    "login",
    "javascript:",
    "mailto:",
    "/search",
)

#: 锚文本整段就是一个年份（「2025年」「2025」）的链接，几乎一定是年度分类入口。
_YEAR_ANCHOR = re.compile(r"^(19|20)\d{2}\s*(年(度)?)?$")

#: 常见「更多/翻页」入口的锚文本；它们指向列表页而不是正文。
_LIST_ANCHORS = frozenset(
    {"更多", "查看更多", "查看全部", "上一页", "下一页", "首页", "尾页", "返回列表", "网站地图"}
)

#: 目录入口文件名。``index``/``list`` 已由 ``_NON_ARTICLE_HINTS`` 覆盖，这里补常见的别名。
_LISTING_FILENAMES = frozenset(
    {"default.htm", "default.html", "default.shtml", "more.htm", "more.html", "more.shtml"}
)

#: 判定「列表/导航页」的最小链接数：少于这个数时，链接占比再高也可能只是正文里的引用。
_LISTING_MIN_LINKS = 12
#: 锚文本字数占正文比例达到这个值（百分比）就按列表页处理：正文页的链接通常远低于此。
_LISTING_LINK_TEXT_PERCENT = 40


class FetchError(Exception):
    """抓取失败。``category`` 是写进运行记录的**可区分终态**，不是内部异常名。"""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ 网址规范


def canonicalize_url(raw: str) -> str | None:
    """规范化成判重用的形式；不可用时返回 ``None``。

    只做「同一资源的不同写法」归一：小写 scheme/host、去默认端口、去 fragment、
    去掉跟踪参数（``utm_*`` 等）、根路径补 ``/``。**不**改路径本身——路径大小写
    在某些站点上是有意义的，改了会把两条不同的政策判成同一条。
    """

    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    query = "&".join(
        part
        for part in parts.query.split("&")
        if part and not _is_tracking_param(part)
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking_param(part: str) -> bool:
    name = part.split("=", 1)[0].lower()
    return name.startswith("utm_") or name in {"spm", "from", "share_token"}


def require_official_url(url: str, field: str = "sourceUrl") -> None:
    """静态白名单检查：https、不带凭据、gov.cn 及其子域、不是 IP 字面量。

    与 ``agents/ingestion.validate_official_url`` 同一套规则，但错误用
    :class:`FetchError`：设置接口要把它翻译成 422，抓取器要把它记成 ``rejected``。
    """

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise FetchError("rejected", f"{field} 不是合法网址。") from exc

    if parts.scheme != "https":
        raise FetchError("rejected", f"{field} 必须是 https 地址。")
    if parts.username or parts.password:
        raise FetchError("rejected", f"{field} 不能带用户名或密码。")
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchError("rejected", f"{field} 的端口不合法。") from exc
    if port not in (None, 443):
        raise FetchError("rejected", f"{field} 只能使用默认 443 端口。")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise FetchError("rejected", f"{field} 缺少主机名。")
    if host == "localhost" or "." not in host:
        raise FetchError("rejected", f"{field} 不能指向本机。")
    if host.endswith((".local", ".internal", ".localhost")):
        raise FetchError("rejected", f"{field} 不能指向内网域名。")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not _is_public(address):
        raise FetchError("rejected", f"{field} 不能直接使用内网或保留地址。")

    if not any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES):
        raise FetchError("rejected", f"{field} 不在官方来源范围内（gov.cn 及其子域）。")


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def resolve_public_addresses(host: str, port: int = 443) -> list[str]:
    """解析主机名并**全部**校验；任何一个地址不公网就整体拒绝。

    只校验首选地址会留下一个口子：多 A 记录的站点可以把内网地址排在第二位，
    下次解析轮到时就连上去了。
    """

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError("dns_failed", f"无法解析主机名 {host}。") from exc

    addresses: list[str] = []
    for info in infos:
        raw = info[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if not _is_public(address):
            raise FetchError("rejected", f"主机名 {host} 解析到非公网地址，已拒绝。")
        if raw not in addresses:
            addresses.append(raw)
    if not addresses:
        raise FetchError("dns_failed", f"主机名 {host} 没有可用地址。")
    return addresses


# ------------------------------------------------------------------ 抓取


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    address: str


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """一次抓取的产物。``text`` 是给抽取用的正文，``raw`` 是原始字节（留证）。"""

    url: str
    status: int
    content_type: str
    text: str
    raw: bytes
    raw_hash: str
    content_hash: str
    fetched_at: datetime
    attachments: tuple[str, ...] = field(default=())
    #: 页面上看起来是附件、但地址不能进候选的链接（例如 http://）。不抓取，只用来
    #: 让审核员知道「这一页确实还有附件」，避免把过滤后的空列表当成没有附件。
    unsupported_attachments: tuple[str, ...] = field(default=())
    #: 解码后的 HTML 原文；正文页用它找附件链接，列表页用它找候选链接。
    markup: str | None = None

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type


Opener = Callable[[str, str, int, str, float, int], HttpResponse]


def _socket_get(
    host: str, address: str, port: int, target: str, timeout: float, max_bytes: int
) -> HttpResponse:
    """连到**已校验的地址**，但 TLS 证书仍按主机名校验。"""

    context = ssl.create_default_context()
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        tls = context.wrap_socket(sock, server_hostname=host)
    except BaseException:
        sock.close()
        raise
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        conn.sock = tls
        conn.request(
            "GET",
            target,
            headers={
                "Host": host,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.1",
                # 不要求压缩：省得为「解压后超过 2 MiB」再写一层判断。
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        body = response.read(max_bytes + 1)
        return HttpResponse(
            status=response.status, headers=headers, body=body, address=address
        )
    finally:
        try:
            tls.close()
        except OSError:
            pass


class Fetcher:
    """受限制的 HTTP 抓取器：白名单、逐跳复检、体积/超时/同站间隔。"""

    def __init__(
        self,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_bytes: int = MAX_BODY_BYTES,
        min_host_interval: float = MIN_HOST_INTERVAL_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        resolve: Callable[[str, int], list[str]] = resolve_public_addresses,
        opener: Opener = _socket_get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._min_host_interval = min_host_interval
        self._max_redirects = max_redirects
        self._resolve = resolve
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._last_host_at: dict[str, float] = {}

    def fetch(self, url: str) -> FetchedPage:
        target = url
        for _hop in range(self._max_redirects + 1):
            response = self._get_once(target)
            location = response.headers.get("location")
            if response.status in _REDIRECT_STATUSES and location:
                target = urljoin(target, location)
                continue
            return self._to_page(target, response)
        raise FetchError("rejected", f"重定向超过 {self._max_redirects} 跳，已停止。")

    def _get_once(self, url: str) -> HttpResponse:
        require_official_url(url)
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or 443
        addresses = self._resolve(host, port)
        self._respect_host_interval(host)
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        try:
            response = self._opener(
                host, addresses[0], port, target, self._timeout, self._max_bytes
            )
        except FetchError:
            raise
        except socket.timeout as exc:
            raise FetchError("timeout", f"{host} 响应超时。") from exc
        except ssl.SSLError as exc:
            raise FetchError("tls_failed", f"{host} 的 TLS 握手失败。") from exc
        except OSError as exc:
            raise FetchError("connect_failed", f"连接 {host} 失败。") from exc
        if len(response.body) > self._max_bytes:
            raise FetchError(
                "oversize", f"{host} 的响应超过 {self._max_bytes // (1024 * 1024)} MiB 上限。"
            )
        if response.status >= 400:
            raise FetchError("http_error", f"{host} 返回 HTTP {response.status}。")
        return response

    def _respect_host_interval(self, host: str) -> None:
        last = self._last_host_at.get(host)
        if last is not None:
            wait = self._min_host_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_host_at[host] = self._clock()

    def _to_page(self, url: str, response: HttpResponse) -> FetchedPage:
        content_type = (response.headers.get("content-type") or "").lower()
        raw = response.body
        markup: str | None = None
        attachments: tuple[str, ...] = ()
        unsupported_attachments: tuple[str, ...] = ()
        if looks_like_pdf(raw, content_type):
            content_type = "application/pdf"
            text = extract_pdf_text(raw)
        else:
            decoded = _decode(raw, content_type)
            if _is_html(content_type, decoded):
                markup = decoded
                text = html_to_text(decoded)
                attachments, unsupported_attachments = scan_attachments(decoded, url)
            elif _is_textual(content_type) and not _looks_binary(raw):
                text = decoded
            else:
                # DOCX/ZIP/图片解出来是乱码：硬当正文会在候选里堆垃圾，还会白烧一次模型。
                # 明确判失败，让来源状态与 failed_articles 把「类型不支持」如实记出来。
                raise FetchError(
                    "unsupported_content",
                    f"不支持的内容类型 {content_type or '（未声明）'}；本版只解析 HTML、纯文本与 PDF。",
                )
        normalized = normalize_text(text)
        return FetchedPage(
            url=url,
            status=response.status,
            content_type=content_type or "text/html",
            text=normalized,
            raw=raw,
            raw_hash=sha256_hex(raw),
            content_hash=sha256_hex(normalized.encode("utf-8")),
            fetched_at=datetime.now(timezone.utc),
            attachments=attachments,
            unsupported_attachments=unsupported_attachments,
            markup=markup,
        )


def _is_html(content_type: str, sample: str) -> bool:
    if "html" in content_type:
        return True
    head = sample.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


#: 可以**按文本**当正文解析的类型。其余类型只有在嗅探确认是 HTML/PDF 时才放行：
#: 政府栏目的链接偶尔直接指向 .docx 或压缩包，二进制硬解出来只会污染候选。
_TEXTUAL_TYPES = frozenset(
    {"application/xhtml+xml", "application/xml", "application/json", "application/rss+xml"}
)


def _is_textual(content_type: str) -> bool:
    base = content_type.split(";")[0].strip()
    # 没声明类型时不拦：很多政府站点省略 content-type，凭 NUL 字节判二进制更可靠。
    return not base or base.startswith("text/") or base in _TEXTUAL_TYPES


def _looks_binary(raw: bytes) -> bool:
    """二进制嗅探：正常网页没有 NUL 字节，DOCX/ZIP/图片一定在头部附近就有。"""

    return b"\x00" in raw[:2048]


def _decode(raw: bytes, content_type: str) -> str:
    charset = ""
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset":
            charset = value.strip().strip('"').strip("'")
    candidates = [charset] if charset else []
    # 大量政府站点仍是 GB18030：先按声明解，再按 UTF-8，最后才用有损替换。
    candidates += ["utf-8-sig", "gb18030"]
    for name in candidates:
        try:
            return raw.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def looks_like_pdf(raw: bytes, content_type: str = "") -> bool:
    return "pdf" in content_type or b"%PDF-" in raw[:1024]


def extract_pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(raw), strict=False)
    except (PdfReadError, ValueError, OSError) as exc:
        raise FetchError("unsupported_content", "无法解析该 PDF。") from exc
    if reader.is_encrypted:
        raise FetchError("unsupported_content", "不支持加密 PDF。")
    try:
        chunks = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - pypdf 对坏文件抛的异常类型不统一
        raise FetchError("unsupported_content", "PDF 文本抽取失败。") from exc
    return "\n".join(chunks)


# ------------------------------------------------------------------ 解析


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        #: 与 ``links`` 一一对应的锚文本，用来识别「2025年」这类年度分类入口。
        self.anchors: list[list[str]] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                href = value.strip()
                self.links.append(href)
                self.anchors.append([href, ""])
                self._current = self.anchors[-1]
                break

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current[1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._current = None


class _TextParser(HTMLParser):
    """把 HTML 变成可读文本：跳过脚本/样式，块级元素换行，空白折叠。"""

    _BLOCK = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}
    )
    _SKIP = frozenset({"script", "style", "noscript", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(markup: str) -> str:
    parser = _TextParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - 坏 HTML 也要尽量给出正文
        return markup
    return normalize_text(parser.text())


def normalize_text(text: str) -> str:
    """统一的正文形式：去行首尾空白、连续空白折成一个空格、连续空行折成一个。"""

    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # ``\s`` 覆盖全角空格与 U+2002 之类的排版空格：不同站点用不同空白字符分隔
        # 同一句话时，规范化后必须得到同一个字符串，否则跨站判重会因不可见字符失效。
        collapsed = re.sub(r"\s+", " ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines).strip()


#: 「像正文」的最短规范化文本。栏目页、框架页、跳转页通常只有几个字甚至没有正文；
#: 政策正文再短也明显超过这个长度。按长度而不是关键词判断，避免把某类政策漏掉。
MIN_ARTICLE_CHARS = 60

#: 计算正文指纹时只看这么多字以上的句子：导航、面包屑、页脚一般很短，条款一般较长。
#: 阈值取 40 是为了把「一、……」「二、……」这类完整条款留在集合里，同时滤掉页脚。
_FINGERPRINT_SENTENCE_CHARS = 40

#: 正文指纹至少要有两句可比较的句子才可靠；少于这个数（例如只有一句话的通知）退回整页哈希。
_FINGERPRINT_MIN_SENTENCES = 2

#: 短句纳入正文指纹的下限：再短就与导航条目分不开。带数字的短句要参与比较——
#: 截止日期、金额、人数、比例常常单独成句，只比长句会把「2026 改 2027」这类
#: 实质变更当成同一份公告。
_FINGERPRINT_SHORT_CHARS = 12

#: 这些前缀的短句是站点元数据（各站的日期格式、字段名并不统一），不参与比较。
_METADATA_PREFIXES = (
    "发文字号",
    "文号",
    "成文日期",
    "发布日期",
    "印发日期",
    "索引号",
    "主题分类",
    "发文机关",
    "有效性",
    "来源：",
)

#: 只有日期的整句（``2023年03月26日`` / ``2023年3月26日``）同样是元数据。
_DATE_ONLY = re.compile(r"^[0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日$")

#: 页脚、备案、版权这类「长但 Everybody 都一样或各站不同」的句子不进正文指纹。
#: 只排除明确的站点样板文字，不做关键词联想：正文里出现这些词的概率极低。
_CHROME_MARKERS = (
    "ICP备",
    "公网安备",
    "网站标识码",
    "版权所有",
    "主办单位",
    "承办单位",
    "技术支持",
    "网站地图",
    "主办：",
    "承办：",
    "地址：",
    "邮编：",
)


def looks_like_article(text: str) -> bool:
    """正文长度粗判。

    首批栏目里有把框架页直接配成来源的情况：页面 200、正文为空或只有「点击跳转」，
    老逻辑会把它当单篇正文，产出一条 ``extraction_failed`` 伪候选。这个判断只用于
    **拦截明显不是正文的页面**，不替代人工核对；阈值以下不判失败，只是不当作正文。
    """

    return len(text.strip()) >= MIN_ARTICLE_CHARS


def looks_like_listing(markup: str, text: str) -> bool:
    """链接文字占大半页的页面是列表/导航页，不是单篇正文。

    URL 过滤挡不住所有入口页：站点可能把目录挂在没有斜杠、也没有 ``index``/``list``
    的路径上。这一层在**抽取之前**按页面结构再拦一次，避免把年度分类页/导航页
    当成正文花掉模型预算、产出伪候选（#64 复审 P1）。阈值取「不少于 12 条链接且
    锚文本占正文 40% 以上」：真实条目页的链接文字占比通常只有几个百分点。
    """

    body = text.strip()
    parser = _LinkParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - 解析不了就交给正文长度判断
        return False
    if len(parser.anchors) < _LISTING_MIN_LINKS:
        return False
    anchor_chars = sum(len(re.sub(r"\s+", "", anchor)) for _, anchor in parser.anchors)
    return anchor_chars * 100 >= len(body) * _LISTING_LINK_TEXT_PERCENT


def _fingerprint_sentences(text: str) -> set[str]:
    """参与正文指纹的句子：长条款 + 带数字的短句（排除站点元数据）。"""

    picked: set[str] = set()
    for raw in re.split(r"[。！？；\n]", text):
        item = raw.strip()
        if not item or any(marker in item for marker in _CHROME_MARKERS):
            continue
        if len(item) >= _FINGERPRINT_SENTENCE_CHARS:
            picked.add(item)
        elif (
            len(item) >= _FINGERPRINT_SHORT_CHARS
            and any(character.isdigit() for character in item)
            and not item.startswith(_METADATA_PREFIXES)
            and not _DATE_ONLY.match(item)
        ):
            picked.add(item)
    return picked


def body_fingerprint(text: str) -> str | None:
    """与导航、页脚无关的正文指纹：句子集合去重排序后取 SHA-256。

    同一个公告被不同站点转载时，整页文本（导航、页脚各不相同）哈希不同，但正文
    条款逐字相同。按句子集合比对能把这类跨站转载认成同一份内容。

    集合里既有长条款，也有**带数字的短句**（截止日、金额、人数、比例常单独成句）：
    只比长句会让「截止日 2026 改 2027」这种实质变更在判重时看不出来。站点元数据
    （发文字号、成文日期、纯日期行）各站写法不一致，排除在外。

    句子不足两句时无法可靠区分，返回 ``None``，调用方退回整页文本哈希判重。
    """

    sentences = _fingerprint_sentences(text)
    if len(sentences) < _FINGERPRINT_MIN_SENTENCES:
        return None
    payload = "\n".join(sorted(sentences))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_listing_link(url: str, anchor: str) -> bool:
    """目录/导航入口而不是单篇正文。

    栏目根页上常见的两类噪声：``./gfxwj2025/`` 这样的**年度分类**（正文页的链接
    一般指向 ``.../202504/t20250401_123456.html``），以及 ``../../zfxxgkzn/``
    这样的**栏目导航**。两者都以 ``/`` 结尾；个别站点不用斜杠时，靠锚文本补判。
    """

    path = urlsplit(url).path
    if path.endswith("/"):
        return True
    last = path.rsplit("/", 1)[-1].lower()
    if last in _LISTING_FILENAMES:
        return True
    text = re.sub(r"\s+", "", anchor)
    if _YEAR_ANCHOR.match(text) or text in _LIST_ANCHORS:
        return True
    return False


def _iter_candidates(markup: str, base_url: str) -> Iterable[tuple[str, str]]:
    """通过白名单/附件/导航过滤的链接及锚文本（尚未排除目录入口）。"""

    parser = _LinkParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - 解析失败就当这一页没有链接
        return
    for href, anchor in parser.anchors:
        absolute = urljoin(base_url, href)
        canonical = canonicalize_url(absolute)
        if canonical is None:
            continue
        if not _is_official(canonical):
            continue
        # 正文抓取只接受 https（``require_official_url``），http 链接必然在连接前被拒；
        # 留在列表里只会把它每轮都算成一次正文失败。
        if urlsplit(canonical).scheme != "https":
            continue
        path = urlsplit(canonical).path.lower()
        if path.endswith(_ATTACHMENT_SUFFIXES):
            continue
        lowered = canonical.lower()
        if any(hint in lowered for hint in _NON_ARTICLE_HINTS):
            continue
        yield canonical, anchor


def listing_links(markup: str, base_url: str) -> list[str]:
    """被链接范围过滤掉的目录/导航入口（年度分类、栏目索引）。

    运行器发现「过滤后一条正文链接都不剩」时用它区分两种来源：页面根本没有可用
    链接（框架页），还是只列出了下一级目录（需要改配正文入口）。
    """

    seen: list[str] = []
    for canonical, anchor in _iter_candidates(markup, base_url):
        if canonical in seen:
            continue
        if _is_listing_link(canonical, anchor):
            seen.append(canonical)
    return seen


def extract_links(markup: str, base_url: str) -> list[str]:
    """页面里的候选链接：规范化、去重、只留官方域名、排除导航与附件。"""

    seen: list[str] = []
    for canonical, anchor in _iter_candidates(markup, base_url):
        if canonical in seen:
            continue
        # 栏目页上的年度分类、栏目导航不是正文。旧逻辑会照抓，年度页正文虽短但
        # 仍过得了 60 字门槛，先花掉抽取预算、再产出伪候选（#64 复审 P1）。
        if _is_listing_link(canonical, anchor):
            continue
        seen.append(canonical)
        if len(seen) >= MAX_LINKS_PER_PAGE:
            break
    return seen


def scan_attachments(
    markup: str, base_url: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """页面上指向附件的链接：分成「能进候选的」和「只能提示的」两组。

    契约里的 ``OfficialUrl`` 要求 https，所以页面上的 ``http://`` 附件地址不能进
    ``attachmentUrls``。但它**不能就此消失**：如果页面只有 http 附件，过滤后列表为空，
    列表页会显示 0 个附件、也没有未抽取提示，审核员就会把只覆盖正文的草稿当成完整
    政策。因此这里把这类链接单独返回，由候选记下来并给出可见提示。

    首版不下载附件，所以只需过滤，不需要逐跳复检（将来真下载附件时必须补上与正文
    相同的 SSRF 防护）。
    """

    parser = _LinkParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001
        return (), ()
    supported: list[str] = []
    unsupported: list[str] = []
    for href in parser.links:
        absolute = urljoin(base_url, href)
        canonical = canonicalize_url(absolute)
        if canonical is None or not _is_official(canonical):
            continue
        if not urlsplit(canonical).path.lower().endswith(_ATTACHMENT_SUFFIXES):
            continue
        target = supported if urlsplit(canonical).scheme == "https" else unsupported
        if canonical not in target:
            target.append(canonical)
    return tuple(supported[:20]), tuple(unsupported[:20])


def find_attachments(markup: str, base_url: str) -> tuple[str, ...]:
    """页面上能进候选的附件链接（https）：只记录、不下载，留给审核员人工打开。"""

    return scan_attachments(markup, base_url)[0]


def _is_official(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def estimate_tokens(text: str) -> int:
    """粗估 prompt token 数：中文约 1.5 字符 / token，英文约 4 字符 / token。

    只用**保守上限**（一律按 1.5）估，宁可少抓一页也不错发一轮超额请求；
    真实用量以模型返回为准，估算只用于「发请求前」的预算闸门。
    """

    return int(len(text) / 1.5) + 64


def summary_lines(items: Iterable[str], limit: int = 5) -> str:
    rows = list(items)[:limit]
    return "；".join(rows)
