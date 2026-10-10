"""Regression tests for Windows/.NET MIME-encoded Unicode PDF filenames.

No external model, database, or real PDF file is required.
"""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from sociology_research.api.dependencies import get_services
from sociology_research.api.routes.imports import _normalized_pdf_filename, _read_pdf_upload, router


class CapturePaperImports:
    def __init__(self) -> None:
        self.single: list[tuple[str, bytes]] = []
        self.batch: list[list[tuple[str, bytes]]] = []

    def start_import(self, name: str, data: bytes) -> str:
        self.single.append((name, data))
        return "test-single"

    def start_batch_import(self, files: list[tuple[str, bytes]]) -> str:
        self.batch.append(files)
        return "test-batch"


@pytest.fixture
def upload_client() -> tuple[TestClient, CapturePaperImports]:
    storage = CapturePaperImports()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(papers=storage)
    return TestClient(app), storage


def _encoded_word(filename: str) -> str:
    encoded = base64.b64encode(filename.encode("utf-8")).decode("ascii")
    return f"=?utf-8?B?{encoded}?="


def test_old_dotnet_rfc2047_encoded_unicode_filename(upload_client):
    client, capture = upload_client
    original = "人工智能社会化：内容生产研究.pdf"
    response = client.post(
        "/api/v1/papers/import",
        files={"file": (_encoded_word(original), b"%PDF-1.4\ntest", "application/pdf")},
    )
    assert response.status_code == 202, response.text
    assert capture.single == [(original, b"%PDF-1.4\ntest")]


def test_plain_unicode_filename_still_work(upload_client):
    client, capture = upload_client
    original = "观念系统——关联类别分析.PDF"
    response = client.post(
        "/api/v1/papers/import",
        files={"file": (original, b"%PDF-test", "application/pdf")},
    )
    assert response.status_code == 202, response.text
    assert capture.single[0][0] == original


def test_new_dotnet_rfc5987_filename_star_preserves_unicode(upload_client):
    client, capture = upload_client
    original = "中国农村居民观念变化.pdf"
    boundary = "sra-upload-test-boundary"
    body = (
        f"--{boundary}\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"upload.pdf\"; "
        f"filename*=utf-8''{quote(original)}\r\n"
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii") + b"%PDF-1.7\n" + f"\r\n--{boundary}--\r\n".encode("ascii")
    response = client.post(
        "/api/v1/papers/import",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 202, response.text
    assert capture.single == [(original, b"%PDF-1.7\n")]


def test_batch_handles_encoded_and_plain_names(upload_client):
    client, capture = upload_client
    first = "科学社会学.pdf"
    second = "paper-2.pdf"
    response = client.post(
        "/api/v1/papers/import-batch",
        files=[
            ("files", (_encoded_word(first), b"%PDF-a", "application/pdf")),
            ("files", (second, b"%PDF-b", "application/pdf")),
        ],
    )
    assert response.status_code == 202, response.text
    assert capture.batch == [[(first, b"%PDF-a"), (second, b"%PDF-b")]]


def test_encoded_non_pdf_is_still_rejected(upload_client):
    client, capture = upload_client
    response = client.post(
        "/api/v1/papers/import",
        files={"file": (_encoded_word("伪装文件.exe"), b"bytes", "application/pdf")},
    )
    assert response.status_code == 415, response.text
    assert not capture.single


def test_empty_pdf_rejected(upload_client):
    client, capture = upload_client
    response = client.post(
        "/api/v1/papers/import",
        files={"file": (_encoded_word("空文档.pdf"), b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert not capture.single


def test_path_components_and_control_characters():
    assert _normalized_pdf_filename(_encoded_word("C:\\papers\\中文论文.pdf")) == "中文论文.pdf"
    assert _normalized_pdf_filename("../paper.pdf") == "paper.pdf"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _normalized_pdf_filename("bad\x00name.pdf")
    assert exc.value.status_code == 400


def test_desktop_multipart_explicitly_uses_filename_star():
    root = Path(__file__).resolve().parents[1]
    source = (root / "desktop" / "SRA.Desktop" / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")
    assert "FileNameStar = originalName" in source
    assert 'FileName = "upload.pdf"' in source
    assert 'AddPdfFilePart(form, file, "file", fileName)' in source
    assert 'AddPdfFilePart(form, content, "files", fileName)' in source
    assert 'form.Add(file, "file", string.IsNullOrWhiteSpace(fileName)' not in source


def _simulate_parser_prefers_ascii_fallback(star_value: str, *, fallback: str = "upload.pdf") -> UploadFile:
    """Construct the UploadFile older python-multipart versions create.

    The raw part header retains filename* but file.filename becomes upload.pdf.
    This is deliberately deterministic across Starlette versions.
    """
    header = (
        'form-data; name="file"; filename="' + fallback + '"; '
        "filename*=utf-8''" + star_value
    )
    return UploadFile(
        file=BytesIO(b"%PDF-1.7\n"),
        filename=fallback,
        headers=Headers({"content-disposition": header, "content-type": "application/pdf"}),
    )


def test_filename_star_wins_when_multipart_parser_prefers_ascii_fallback():
    original = "中国农村居民观念变化.pdf"
    upload = _simulate_parser_prefers_ascii_fallback(quote(original))
    assert asyncio.run(_read_pdf_upload(upload)) == (original, b"%PDF-1.7\n")


def test_filename_star_path_components_are_removed():
    upload = _simulate_parser_prefers_ascii_fallback(quote("C:\\papers\\中国论文.pdf"))
    assert asyncio.run(_read_pdf_upload(upload))[0] == "中国论文.pdf"


def test_filename_star_executable_is_rejected_despite_pdf_fallback():
    from fastapi import HTTPException
    upload = _simulate_parser_prefers_ascii_fallback(quote("not-a-pdf.exe"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_pdf_upload(upload))
    assert exc.value.status_code == 415


def test_filename_star_invalid_utf8_rejected_instead_of_using_fallback():
    from fastapi import HTTPException
    upload = _simulate_parser_prefers_ascii_fallback("%FF.pdf")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_pdf_upload(upload))
    assert exc.value.status_code == 400


def test_filename_star_with_quoted_semicolon_and_unicode():
    original = "主题;政策—比较.pdf"
    upload = _simulate_parser_prefers_ascii_fallback(quote(original))
    assert asyncio.run(_read_pdf_upload(upload))[0] == original
