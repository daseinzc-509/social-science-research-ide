"""Local PDF region-review workflow: render -> OCR candidate -> human annotation.

This module does not update parsed source blocks, paper cards or LLM runs.
Original PDF, source block text, OCR proposals and researcher confirmations
have distinct provenance. Only installed PDFium and RapidOCR are used; no API.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegionBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x0: float = Field(ge=0, le=1, allow_inf_nan=False)
    y0: float = Field(ge=0, le=1, allow_inf_nan=False)
    x1: float = Field(ge=0, le=1, allow_inf_nan=False)
    y1: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_rectangle(self):
        if self.x1 - self.x0 < 0.008 or self.y1 - self.y0 < 0.008:
            raise ValueError("Selection must be a rectangle larger than 0.8% of each page dimension")
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bounds: RegionBounds
    manual_only: bool = False


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=16, max_length=100)
    corrected_text: str = Field(min_length=1, max_length=12000)
    researcher_note: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def meaningful_text(self):
        if not self.corrected_text.strip():
            raise ValueError("Confirmed text cannot be blank")
        return self


@dataclass(frozen=True)
class PendingPreview:
    paper_id: str
    pdf_sha256: str
    file_size: int
    file_mtime_ns: int
    page_number: int
    bounds: RegionBounds
    original_text: str
    ocr_text: str
    source_block_ids: tuple[str, ...]
    created: float


class PreviewExpired(ValueError):
    pass


class RegionPreviewRegistry:
    """Short-lived, bounded memory. No OCR candidates are persisted pre-confirmation."""

    def __init__(self, ttl_seconds: int = 1800, limit: int = 32):
        self.ttl_seconds = ttl_seconds
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: dict[str, PendingPreview] = {}

    def add(self, proposal: PendingPreview) -> str:
        identifier = secrets.token_urlsafe(24)
        with self._lock:
            self._purge()
            while len(self._entries) >= self.limit:
                oldest = min(self._entries, key=lambda key: self._entries[key].created)
                self._entries.pop(oldest, None)
            self._entries[identifier] = proposal
        return identifier

    def get(self, preview_id: str, paper_id: str) -> PendingPreview:
        with self._lock:
            self._purge()
            item = self._entries.get(preview_id)
            if item is None or item.paper_id != paper_id:
                raise PreviewExpired("OCR preview expired; select the region and recognize it again")
            return item

    def consume(self, preview_id: str):
        with self._lock:
            self._entries.pop(preview_id, None)

    def _purge(self):
        cutoff = time.monotonic() - self.ttl_seconds
        for key, item in list(self._entries.items()):
            if item.created < cutoff:
                del self._entries[key]


preview_registry = RegionPreviewRegistry()
_ocr_lock = threading.Lock()
_ocr_engine = None


def _rapidocr_lines(image):
    """Read the installed RapidOCR output; never silently invent text."""
    global _ocr_engine
    from rapidocr import RapidOCR

    with _ocr_lock:
        if _ocr_engine is None:
            _ocr_engine = RapidOCR()
        result = _ocr_engine(image)

    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if texts is None and isinstance(result, tuple) and result:
        # The old RapidOCR return shape was (result_rows, duration).
        rows = result[0]
        if isinstance(rows, (tuple, list)):
            texts = [row[1] for row in rows if isinstance(row, (tuple, list)) and len(row) > 1]
            scores = [row[2] for row in rows if isinstance(row, (tuple, list)) and len(row) > 2]
    items = [
        str(value).strip()
        for value in (texts if texts is not None else ())
        if str(value).strip()
    ]
    if not items:
        return "", None
    numeric_scores = [
        float(x)
        for x in (scores if scores is not None else ())
        if isinstance(x, (int, float)) and math.isfinite(float(x))
    ]
    avg_confidence = sum(numeric_scores) / len(numeric_scores) if numeric_scores else None
    return "\n".join(items)[:12000], avg_confidence


def open_pdf_page(pdf_path: Path, page_number: int, scale: float):
    """Return PIL RGB image and PDF page dimensions (points), safely bounded."""
    import pypdfium2 as pdfium

    if not math.isfinite(scale) or not 0.7 <= scale <= 2.0:
        raise ValueError("Page scale must be between 0.7 and 2.0")
    with pdfium.PdfDocument(str(pdf_path)) as document:
        count = len(document)
        if page_number < 1 or page_number > count:
            raise ValueError(f"Page must be in 1..{count}")
        page = document[page_number - 1]
        try:
            width_pt, height_pt = map(float, page.get_size())
            if width_pt <= 0 or height_pt <= 0:
                raise ValueError("PDF page has invalid dimensions")
            if width_pt * height_pt * scale * scale > 15_000_000:
                raise ValueError("This PDF page is too large for interactive preview")
            rendered = page.render(scale=scale)
            image = rendered.to_pil().convert("RGB")
            return image, (width_pt, height_pt), count
        finally:
            page.close()


def pdf_page_dimensions(pdf_path: Path, page_number: int):
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(str(pdf_path)) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"Page must be in 1..{len(document)}")
        page = document[page_number - 1]
        try:
            return tuple(map(float, page.get_size()))
        finally:
            page.close()


def as_pdf_box(bounds: RegionBounds, dimensions: tuple[float, float]):
    width, height = dimensions
    return (
        bounds.x0 * width, bounds.y0 * height,
        bounds.x1 * width, bounds.y1 * height,
    )


def intersecting_blocks(blocks, rectangle: tuple[float, float, float, float]):
    x0, y0, x1, y1 = rectangle
    selected = []
    for block in blocks:
        b = block.bbox
        if b is None or len(b) != 4:
            continue
        bx0, by0, bx1, by1 = map(float, b)
        if max(x0, bx0) < min(x1, bx1) and max(y0, by0) < min(y1, by1):
            selected.append(block)
    return selected


def region_original_text(blocks, max_chars: int = 12000) -> str:
    return "\n\n".join(block.text for block in blocks if block.text.strip())[:max_chars]


def render_png(pdf_path: Path, page_number: int, scale: float = 1.5):
    image, dimensions, count = open_pdf_page(pdf_path, page_number, scale)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), dimensions, count


def recognize_region(
    pdf_path: Path, page_number: int, bounds: RegionBounds,
    *, engine: Callable | None = None,
):
    """OCR only the visually selected page area; never reparse entire document."""
    import numpy as np

    image, dimensions, count = open_pdf_page(pdf_path, page_number, scale=2.0)
    iw, ih = image.size
    crop_box = (
        max(0, min(iw - 1, int(bounds.x0 * iw))),
        max(0, min(ih - 1, int(bounds.y0 * ih))),
        max(1, min(iw, int(math.ceil(bounds.x1 * iw)))),
        max(1, min(ih, int(math.ceil(bounds.y1 * ih)))),
    )
    if crop_box[2] - crop_box[0] < 14 or crop_box[3] - crop_box[1] < 14:
        raise ValueError("Selection is too small for reliable OCR")
    crop = image.crop(crop_box)
    reader = engine or _rapidocr_lines
    # RapidOCR's numpy image interface expects OpenCV's BGR channel order.
    # PIL's RGB pixels must be converted to prevent colored scans degrading.
    recognized, score = reader(np.asarray(crop)[:, :, ::-1].copy())
    return recognized, score, dimensions


class RegionReviewStore:
    """Append-only human confirmations with reversible (audited) revocations."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.database_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS region_reviews (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    pdf_sha256 TEXT NOT NULL,
                    page_number INTEGER NOT NULL CHECK(page_number > 0),
                    bounds_json TEXT NOT NULL,
                    source_block_ids_json TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,
                    ocr_candidate TEXT NOT NULL,
                    corrected_text TEXT NOT NULL,
                    researcher_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_region_reviews_paper "
                "ON region_reviews(paper_id, page_number, created_at)"
            )

    def add(self, paper_id: str, pending: PendingPreview, corrected_text: str, note: str):
        identifier = secrets.token_hex(16)
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO region_reviews
                (id,paper_id,pdf_sha256,page_number,bounds_json,
                 source_block_ids_json,extracted_text,ocr_candidate,corrected_text,
                 researcher_note,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                identifier, paper_id, pending.pdf_sha256, pending.page_number,
                pending.bounds.model_dump_json(),
                json.dumps(pending.source_block_ids, ensure_ascii=False),
                pending.original_text, pending.ocr_text, corrected_text, note, timestamp,
            ))
        return identifier

    def list(self, paper_id: str):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM region_reviews WHERE paper_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 200", (paper_id,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "page_number": row["page_number"],
                "bounds": json.loads(row["bounds_json"]),
                "source_block_ids": json.loads(row["source_block_ids_json"]),
                "extracted_text": row["extracted_text"],
                "ocr_candidate": row["ocr_candidate"],
                "corrected_text": row["corrected_text"],
                "researcher_note": row["researcher_note"],
                "created_at": row["created_at"],
                "revoked_at": row["revoked_at"],
                "provenance": "HUMAN_REVIEWED" if row["revoked_at"] is None else "REVOKED",
            }
            for row in rows
        ]

    def revoke(self, paper_id: str, review_id: str):
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE region_reviews SET revoked_at=? WHERE id=? AND paper_id=? AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), review_id, paper_id),
            )
        if result.rowcount != 1:
            raise KeyError("Active researcher review not found")
