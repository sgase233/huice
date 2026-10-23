"""文件分类、哈希与官方 URL 规则的单元测试（不发网络请求）。"""

from __future__ import annotations

import hashlib

import pytest

from app.agents.ingestion import (
    MAX_PDF_PAGES,
    prepare_sources,
    sha256_hex,
    validate_official_url,
)
from app.errors import ApiProblem
from app.schemas import PolicyImportMetadata
from support import metadata_payload


def metadata(**overrides) -> PolicyImportMetadata:
    return PolicyImportMetadata.model_validate(metadata_payload(**overrides))


def problem(exc_info) -> ApiProblem:
    return exc_info.value


def blank_pdf() -> bytes:
    """一页空白 PDF：合法但抽不出文字。"""
    from io import BytesIO

    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sz.gov.cn/zwgk/zcfg/content/post_1.html",
        "https://sz.gov.cn/",
        "https://GOV.CN/policy",
        "https://www.gov.cn:443/a/b",
    ],
)
def test_official_urls_are_accepted(url):
    validate_official_url(url, "sourceUrl")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sz.gov.cn/a",
        "https://user:pw@www.sz.gov.cn/a",
        "https://www.sz.gov.cn:8443/a",
        "https://localhost/a",
        "https://intranet/a",
        "https://127.0.0.1/a",
        "https://10.0.0.8/a",
        "https://192.168.1.10/a",
        "https://169.254.1.1/a",
        "https://[::1]/a",
        "https://service.local/a",
        "https://service.internal/a",
        "https://evil.example.com/a",
        "https://gov.cn.evil.example.com/a",
        "https://example.gov.cn.evil.com/a",
        "https:///nohost",
        "",
    ],
)
def test_non_official_urls_are_rejected(url):
    with pytest.raises(ApiProblem) as info:
        validate_official_url(url, "sourceUrl")
    assert problem(info).status_code == 422
    assert problem(info).code == "OFFICIAL_URL_REJECTED"
    assert problem(info).details["field"] == "sourceUrl"


def test_sha256_hex_matches_hashlib():
    data = "政策正文".encode()
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()
    assert len(sha256_hex(b"")) == 64


def test_first_file_is_body_and_the_rest_are_attachments():
    prepared = prepare_sources(
        metadata(),
        [
            ("notice.txt", "text/plain", "政策正文".encode()),
            ("annex.txt", "text/plain", "附件内容".encode()),
        ],
    )

    assert [item.kind for item in prepared] == ["body", "attachment"]
    assert prepared[0].text == "政策正文"
    assert prepared[0].content_hash == sha256_hex("政策正文".encode())
    assert prepared[0].byte_size == len("政策正文".encode())


def test_no_files_yields_no_sources():
    assert prepare_sources(metadata(), []) == []


def test_utf8_bom_is_stripped():
    prepared = prepare_sources(metadata(), [("a.txt", None, b"\xef\xbb\xbf2026 \xe6\x94\xbf\xe7\xad\x96")])
    assert prepared[0].text == "2026 政策"


def test_html_file_is_marked_as_html():
    prepared = prepare_sources(
        metadata(),
        [("page.html", "text/html", "<!DOCTYPE html><html><body>正文</body></html>".encode())],
    )
    assert prepared[0].mime_type == "text/html"


def test_pdf_magic_marks_pdf_mime():
    prepared = prepare_sources(
        metadata(),
        [
            ("body.txt", None, "正文".encode()),
            ("a.bin", "application/octet-stream", blank_pdf()),
        ],
    )
    assert prepared[1].mime_type == "application/pdf"
    assert prepared[1].kind == "attachment"


def test_invalid_utf8_is_rejected():
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), [("a.bin", None, b"\xff\xfe\x00\x00binary")])
    assert problem(info).status_code == 415
    assert problem(info).code == "UNSUPPORTED_FILE_TYPE"


def test_nul_bytes_in_text_are_rejected():
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), [("a.txt", None, "正文\x00尾巴".encode())])
    assert problem(info).code == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_is_rejected():
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), [("a.txt", None, b"")])
    assert problem(info).status_code == 422
    assert problem(info).code == "EMPTY_FILE"


def test_whitespace_only_body_is_rejected():
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), [("a.txt", None, b"   \n\t ")])
    assert problem(info).code == "EMPTY_FILE"


def test_blank_attachment_is_allowed():
    prepared = prepare_sources(
        metadata(),
        [("body.txt", None, "正文".encode()), ("empty.txt", None, b"  ")],
    )
    assert prepared[1].kind == "attachment"


def test_broken_pdf_is_reported_as_invalid_pdf():
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), [("a.pdf", "application/pdf", b"%PDF-1.7\n%%EOF\n")])
    assert problem(info).status_code == 422
    assert problem(info).code in {"INVALID_PDF", "PDF_HAS_NO_TEXT"}


def test_too_many_files_are_rejected():
    files = [("a.txt", None, b"x")] * 6
    with pytest.raises(ApiProblem) as info:
        prepare_sources(metadata(), files)
    assert problem(info).status_code == 422
    assert problem(info).code == "TOO_MANY_FILES"


def test_file_name_path_components_are_stripped():
    prepared = prepare_sources(metadata(), [("C:\\temp\\..\\恶意\\政策.txt", None, "正文".encode())])
    assert prepared[0].file_name == "政策.txt"


def test_pdf_page_limit_is_documented():
    assert MAX_PDF_PAGES == 300
