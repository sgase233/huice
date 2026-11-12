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

不执行正文里的任何指令（正文只是数据）、不跟随 JS 跳转、不解 doc/docx/zip 附件，
也不保存 Cookie。附件里的 PDF 会抽取文字；其余格式只记录链接留给审核员。
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
        if looks_like_pdf(raw, content_type):
            content_type = "application/pdf"
            text = extract_pdf_text(raw)
        else:
            decoded = _decode(raw, content_type)
            if _is_html(content_type, decoded):
                markup = decoded
                text = html_to_text(decoded)
                attachments = find_attachments(decoded, url)
            else:
                text = decoded
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
            markup=markup,
        )


def _is_html(content_type: str, sample: str) -> bool:
    if "html" in content_type:
        return True
    head = sample.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(value.strip())


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
        collapsed = re.sub(r"[ \t　\xa0]+", " ", line).strip()
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines).strip()


def extract_links(markup: str, base_url: str) -> list[str]:
    """页面里的候选链接：规范化、去重、只留官方域名、排除导航与附件。"""

    parser = _LinkParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - 解析失败就当这一页没有链接
        return []

    seen: list[str] = []
    for href in parser.links:
        absolute = urljoin(base_url, href)
        canonical = canonicalize_url(absolute)
        if canonical is None or canonical in seen:
            continue
        if not _is_official(canonical):
            continue
        path = urlsplit(canonical).path.lower()
        if path.endswith(_ATTACHMENT_SUFFIXES):
            continue
        lowered = canonical.lower()
        if any(hint in lowered for hint in _NON_ARTICLE_HINTS):
            continue
        seen.append(canonical)
        if len(seen) >= MAX_LINKS_PER_PAGE:
            break
    return seen


def find_attachments(markup: str, base_url: str) -> tuple[str, ...]:
    """页面上指向附件的链接：首版只处理 PDF，其余留给审核员人工打开。"""

    parser = _LinkParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001
        return ()
    found: list[str] = []
    for href in parser.links:
        absolute = urljoin(base_url, href)
        canonical = canonicalize_url(absolute)
        if canonical is None or not _is_official(canonical):
            continue
        if not urlsplit(canonical).path.lower().endswith(_ATTACHMENT_SUFFIXES):
            continue
        if canonical not in found:
            found.append(canonical)
    return tuple(found[:20])


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
