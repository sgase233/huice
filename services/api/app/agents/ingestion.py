"""Policy source ingestion: file classification, hashing and official-URL checks.

Only text extraction happens here. Fetching a URL, following redirects and
resolving DNS belong to the later ingestion step and are deliberately *not*
implemented yet: this module never performs network I/O.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlparse

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..errors import ApiProblem
from ..schemas import PolicyImportMetadata

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 5
MAX_REQUEST_BYTES = MAX_FILES * MAX_FILE_BYTES + 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_PDF_PAGES = 300

OFFICIAL_HOST_SUFFIXES = ("gov.cn",)


@dataclass(frozen=True, slots=True)
class PreparedSource:
    kind: str
    file_name: str
    mime_type: str
    text: str
    content_hash: str
    byte_size: int


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_official_url(url: str, field: str) -> None:
    """Static allow-list check. Does not resolve DNS or fetch the URL."""
    parsed = urlparse(url)
    host = ""

    if parsed.scheme != "https":
        raise _reject(field)
    if parsed.username or parsed.password:
        raise _reject(field)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _reject(field) from exc
    if port not in (None, 443):
        raise _reject(field)

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise _reject(field)

    if host == "localhost" or "." not in host:
        raise _reject(field)
    if host.endswith((".local", ".internal", ".localhost")):
        raise _reject(field)

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise _reject(field)

    if not any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES):
        raise _reject(field)


def _reject(field: str) -> ApiProblem:
    return ApiProblem(
        422,
        "OFFICIAL_URL_REJECTED",
        f"{field} 必须是官方 https 地址（当前白名单为 gov.cn 及其子域），且不能指向本机或内网。",
        {"field": field},
    )


def prepare_sources(
    metadata: PolicyImportMetadata,
    files: list[tuple[str, str | None, bytes]],
) -> list[PreparedSource]:
    """Classify uploaded files; the first one is the policy body, the rest attachments."""
    if len(files) > MAX_FILES:
        raise ApiProblem(422, "TOO_MANY_FILES", "一次最多上传 5 个文件。")

    prepared: list[PreparedSource] = []
    for index, (file_name, content_type, data) in enumerate(files):
        is_body = index == 0
        prepared.append(_prepare_one(file_name, content_type, data, is_body=is_body))
    return prepared


def _prepare_one(
    file_name: str,
    content_type: str | None,
    data: bytes,
    *,
    is_body: bool,
) -> PreparedSource:
    if not data:
        raise ApiProblem(422, "EMPTY_FILE", "上传的文件为空，请检查后重新上传。")
    if len(data) > MAX_FILE_BYTES:
        raise ApiProblem(413, "FILE_TOO_LARGE", "单个文件不能超过 10 MiB。")

    display_name = _basename(file_name)
    content_hash = sha256_hex(data)

    if b"%PDF-" in data[:1024]:
        text = _extract_pdf(data)
        mime_type = "application/pdf"
    else:
        text = _decode_text(data)
        mime_type = "text/html" if _looks_like_html(text) else "text/plain"

    if is_body and not text.strip():
        raise ApiProblem(
            422,
            "PDF_HAS_NO_TEXT" if mime_type == "application/pdf" else "EMPTY_FILE",
            "正文没有可提取的文字，请检查文件或改用其他格式。",
        )

    return PreparedSource(
        kind="body" if is_body else "attachment",
        file_name=display_name,
        mime_type=mime_type,
        text=text,
        content_hash=content_hash,
        byte_size=len(data),
    )


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data), strict=False)
    except (PdfReadError, ValueError, OSError) as exc:
        raise ApiProblem(422, "INVALID_PDF", "无法解析该 PDF，请确认文件未损坏。") from exc

    if reader.is_encrypted:
        raise ApiProblem(422, "ENCRYPTED_PDF", "不支持加密 PDF，请提供未加密版本。")

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # pypdf raises several unrelated types on malformed files
        raise ApiProblem(422, "INVALID_PDF", "无法读取该 PDF 的页面结构。") from exc

    if page_count > MAX_PDF_PAGES:
        raise ApiProblem(422, "PDF_TOO_LONG", f"PDF 页数不能超过 {MAX_PDF_PAGES} 页。")

    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:
            raise ApiProblem(422, "INVALID_PDF", "PDF 文本抽取失败，请确认文件可正常打开。") from exc
    return "\n".join(chunks).strip()


def _decode_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiProblem(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "只支持 PDF 或 UTF-8 文本文件（含 HTML 原文）。",
        ) from exc

    sample = text[:4096]
    control = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r")
    if "\x00" in text or control > max(3, len(sample) // 50):
        raise ApiProblem(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "文件内容看起来是二进制数据，只支持 PDF 或 UTF-8 文本。",
        )
    return text


def _basename(file_name: str) -> str:
    """Keep the file name only: browsers may send a full Windows path."""
    cleaned = (file_name or "").replace("\\", "/").strip()
    return cleaned.rsplit("/", 1)[-1] or "source"


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:512].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")
