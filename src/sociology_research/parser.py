"""PDF parsing boundary with a Docling-first backend and PyMuPDF fallback.

Docling is preferred when installed because it provides model-based layout analysis,
reading order, tables, OCR and typed document items with page/bbox provenance. The
PyMuPDF backend remains available as a lightweight fallback and for regression tests.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from .models import ParseStatus, ParsedDocument, SourceBlock


class DocumentParseError(ValueError):
    """Raised when a PDF cannot be safely parsed."""


class DocumentParser(Protocol):
    version: str

    def parse(
        self,
        pdf_path: Path,
        *,
        paper_id: str,
        sha256: str,
        progress: Callable[[str], None] | None = None,
    ) -> ParsedDocument: ...


def create_default_parser() -> DocumentParser:
    """Return the configured parser.

    SRA_PDF_PARSER may be ``auto`` (default), ``docling`` or ``pymupdf``.
    ``auto`` selects Docling when installed and otherwise keeps the existing
    lightweight PyMuPDF behavior. Installing Docling later therefore upgrades old
    papers automatically through the existing parser-version refresh mechanism.
    """

    requested = os.environ.get("SRA_PDF_PARSER", "auto").strip().casefold() or "auto"
    if requested not in {"auto", "docling", "pymupdf"}:
        raise DocumentParseError(
            "SRA_PDF_PARSER must be one of: auto, docling, pymupdf"
        )
    if requested == "pymupdf":
        return PyMuPDFDocumentParser()
    if _docling_installed():
        return DoclingDocumentParser()
    if requested == "docling":
        raise DocumentParseError(
            "Docling was requested but is not installed. Run install_docling.bat once, "
            "or install 'docling>=2.131,<3' into .conda-env."
        )
    return PyMuPDFDocumentParser()


class DoclingDocumentParser:
    """Semantic PDF parser backed by Docling's standard pipeline."""

    def __init__(self) -> None:
        try:
            version = importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        self.version = f"docling-standard-v2-print-pages:{version}"

    def parse(
        self,
        pdf_path: Path,
        *,
        paper_id: str,
        sha256: str,
        progress: Callable[[str], None] | None = None,
    ) -> ParsedDocument:
        path = _validate_pdf_path(pdf_path)
        _emit(progress, "Docling: inspecting PDF text layer…")
        if not _docling_installed():
            raise DocumentParseError(
                "Docling is not installed. Run install_docling.bat once, or install "
                "'docling>=2.131,<3' into .conda-env."
            )

        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise DocumentParseError(f"Could not import Docling: {exc}") from exc

        try:
            text_profile = _pdf_text_layer_profile(path)
            options = PdfPipelineOptions()
            # OCR is expensive and unnecessary for born-digital PDFs with a reliable text
            # layer. Keep Docling's layout/table models, but only enable OCR for scanned or
            # mixed image-heavy documents.
            options.do_ocr = text_profile.needs_ocr
            options.do_table_structure = True
            if hasattr(options, "generate_page_images"):
                options.generate_page_images = False
            if hasattr(options, "generate_picture_images"):
                options.generate_picture_images = False
            if text_profile.needs_ocr:
                _emit(
                    progress,
                    f"Docling: {text_profile.text_pages}/{text_profile.page_count} pages have usable embedded text; OCR enabled.",
                )
            else:
                _emit(
                    progress,
                    f"Docling: selectable text detected on {text_profile.text_pages}/{text_profile.page_count} pages; OCR disabled for this PDF.",
                )

            # Prefer accurate table structure when the installed Docling exposes the mode.
            try:
                from docling.datamodel.pipeline_options import TableFormerMode

                options.table_structure_options.mode = TableFormerMode.ACCURATE
            except Exception:
                pass

            _emit(
                progress,
                f"Docling: loading layout/table models and converting {text_profile.page_count or '?'} pages…",
            )
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            result = _convert_with_heartbeat(converter, path, progress=progress)
            document = result.document
            _emit(progress, "Docling: conversion finished; building provenance blocks…")
        except Exception as exc:  # pragma: no cover - depends on optional package/models
            raise DocumentParseError(f"Docling could not parse PDF: {exc}") from exc

        warnings: list[str] = []
        status_value = str(getattr(result, "status", "")).casefold()
        if status_value and "success" not in status_value:
            warnings.append(f"Docling conversion status needs review: {getattr(result, 'status', status_value)}")

        blocks, adapter_warnings = _blocks_from_docling_document(document, paper_id=paper_id)
        warnings.extend(adapter_warnings)
        # Printed page numbers are deceptively hard for layout models: journals often
        # encode 114 as three separate glyphs, and Docling may only label some pages as
        # PAGE_FOOTER. Add one deterministic furniture block per page from the PDF text
        # layer so page-range recovery is independent of Docling's semantic labeling.
        printed_page_blocks = _extract_printed_page_number_blocks(path, paper_id=paper_id)
        blocks.extend(printed_page_blocks)
        page_count = len(getattr(document, "pages", {}) or {})
        if page_count < 1:
            page_count = max((block.page_number for block in blocks), default=0)
        if page_count < 1:
            raise DocumentParseError("Docling produced no pages")

        body_pages = {
            block.page_number
            for block in blocks
            if block.role != "furniture" and block.text.strip()
        }
        empty_pages = [page for page in range(1, page_count + 1) if page not in body_pages]
        if empty_pages:
            preview = ", ".join(map(str, empty_pages[:12]))
            suffix = "…" if len(empty_pages) > 12 else ""
            warnings.append(
                f"Docling produced no body content on page(s): {preview}{suffix}; review OCR/layout output."
            )

        status = ParseStatus.NEEDS_REVIEW if warnings else ParseStatus.SUCCESS
        _emit(progress, f"Docling: parsed {len(blocks)} structured source blocks across {page_count} pages.")
        return ParsedDocument(
            paper_id=paper_id,
            sha256=sha256,
            source_path=str(path.resolve()),
            page_count=page_count,
            status=status,
            parser_version=self.version,
            warnings=warnings,
            blocks=blocks,
        )


class PyMuPDFDocumentParser:
    version = "pymupdf-tables-v3-print-pages"

    def parse(
        self,
        pdf_path: Path,
        *,
        paper_id: str,
        sha256: str,
        progress: Callable[[str], None] | None = None,
    ) -> ParsedDocument:
        path = _validate_pdf_path(pdf_path)
        _emit(progress, "PyMuPDF fallback: opening PDF…")
        try:
            import fitz
        except ImportError as exc:
            raise DocumentParseError(
                "PyMuPDF is not installed. Install project dependencies with: python -m pip install -e ."
            ) from exc

        try:
            document = fitz.open(path)
        except Exception as exc:
            raise DocumentParseError(f"Could not open PDF: {exc}") from exc

        try:
            if document.is_encrypted:
                raise DocumentParseError("Encrypted PDFs are not supported")
            if document.page_count < 1:
                raise DocumentParseError("PDF contains no pages")

            blocks: list[SourceBlock] = []
            warnings: list[str] = []
            empty_pages: list[int] = []
            for page_index, page in enumerate(document, start=1):
                if page_index == 1 or page_index == document.page_count or page_index % 5 == 0:
                    _emit(progress, f"PyMuPDF fallback: parsing page {page_index}/{document.page_count}…")
                table_blocks: list[SourceBlock] = []
                table_boxes: list[tuple[float, float, float, float]] = []
                try:
                    found_tables = page.find_tables()
                    for table_index, table in enumerate(found_tables.tables):
                        rows = [
                            [" ".join((cell or "").split()) for cell in row]
                            for row in (table.extract() or [])
                        ]
                        rows = [row for row in rows if any(cell for cell in row)]
                        if not rows:
                            continue
                        bbox = tuple(float(value) for value in table.bbox)
                        table_boxes.append(bbox)
                        table_text = "\n".join("\t".join(row) for row in rows)
                        table_blocks.append(
                            SourceBlock(
                                id=_stable_block_id(paper_id, page_index, "table", table_index, table_text),
                                page_number=page_index,
                                text=table_text,
                                bbox=bbox,
                                table_rows=rows,
                                role="table",
                                source_label="pymupdf:table",
                            )
                        )
                except Exception as exc:
                    warnings.append(f"Table extraction needs review on page {page_index}: {exc}")

                page_content: list[SourceBlock] = list(table_blocks)
                page_blocks = page.get_text("blocks", sort=True)
                extracted = len(table_blocks)
                for block_index, item in enumerate(page_blocks):
                    text = str(item[4]).strip()
                    if not text:
                        continue
                    text_box = (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
                    if any(_overlap_ratio(text_box, table_box) >= 0.5 for table_box in table_boxes):
                        continue
                    extracted += 1
                    page_content.append(
                        SourceBlock(
                            id=_stable_block_id(paper_id, page_index, "text", block_index, text),
                            page_number=page_index,
                            text=text,
                            bbox=text_box,
                            role="body",
                            source_label="pymupdf:text",
                        )
                    )
                printed = _extract_printed_page_number_from_page(page, page_index, paper_id)
                if printed is not None:
                    page_content.append(printed)
                page_content.sort(key=_reading_order_key)
                blocks.extend(page_content)
                if extracted == 0:
                    empty_pages.append(page_index)

            if empty_pages:
                preview = ", ".join(map(str, empty_pages[:12]))
                suffix = "…" if len(empty_pages) > 12 else ""
                warnings.append(f"No selectable text found on page(s): {preview}{suffix}; OCR is not supported by the fallback parser.")

            status = ParseStatus.NEEDS_REVIEW if empty_pages else ParseStatus.SUCCESS
            return ParsedDocument(
                paper_id=paper_id,
                sha256=sha256,
                source_path=str(path.resolve()),
                page_count=document.page_count,
                status=status,
                parser_version=self.version,
                warnings=warnings,
                blocks=blocks,
            )
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(f"Could not extract PDF text: {exc}") from exc
        finally:
            document.close()



class _TextLayerProfile:
    def __init__(self, *, page_count: int, text_pages: int, text_chars: int, needs_ocr: bool):
        self.page_count = page_count
        self.text_pages = text_pages
        self.text_chars = text_chars
        self.needs_ocr = needs_ocr


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _pdf_text_layer_profile(path: Path) -> _TextLayerProfile:
    """Cheaply decide whether OCR is needed without asking Docling to OCR everything.

    A page counts as text-bearing when PyMuPDF can extract at least 40 non-whitespace
    characters. OCR is enabled for image-only or strongly mixed PDFs; born-digital
    journal articles therefore avoid the OCR model entirely.
    """

    try:
        import fitz

        doc = fitz.open(path)
    except Exception:
        # Conservative fallback: if we cannot inspect the embedded text layer, keep OCR.
        return _TextLayerProfile(page_count=0, text_pages=0, text_chars=0, needs_ocr=True)

    try:
        page_count = int(doc.page_count)
        text_pages = 0
        text_chars = 0
        for page in doc:
            compact = re.sub(r"\s+", "", page.get_text("text") or "")
            text_chars += len(compact)
            if len(compact) >= 40:
                text_pages += 1
        # Requiring >=80% text-bearing pages keeps OCR on for materially mixed/scanned PDFs.
        required = max(1, (page_count * 4 + 4) // 5) if page_count else 1
        needs_ocr = page_count == 0 or text_pages < required or text_chars < max(200, page_count * 80)
        return _TextLayerProfile(
            page_count=page_count,
            text_pages=text_pages,
            text_chars=text_chars,
            needs_ocr=needs_ocr,
        )
    finally:
        doc.close()


def _convert_with_heartbeat(converter: object, path: Path, *, progress: Callable[[str], None] | None):
    """Run Docling conversion while periodically reporting that CPU work is alive."""

    if progress is None:
        return converter.convert(path)

    stop = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stop.wait(15.0):
            elapsed = int(time.monotonic() - started)
            progress(f"Docling: converting document on CPU… {elapsed}s elapsed")

    worker = threading.Thread(target=heartbeat, name="sra-docling-heartbeat", daemon=True)
    worker.start()
    try:
        return converter.convert(path)
    finally:
        stop.set()
        worker.join(timeout=0.2)

def _blocks_from_docling_document(document: object, *, paper_id: str) -> tuple[list[SourceBlock], list[str]]:
    """Adapt a DoclingDocument to the engine's stable SourceBlock contract.

    Kept separate from the optional imports so it can be regression-tested with
    lightweight fake Docling objects and so API drift is localized to one adapter.
    """

    warnings: list[str] = []
    blocks: list[SourceBlock] = []

    try:
        from docling_core.types.doc import ContentLayer

        included_layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
        iterator = document.iterate_items(included_content_layers=included_layers)
    except Exception:
        iterator = document.iterate_items()

    for item_index, pair in enumerate(iterator):
        item = pair[0] if isinstance(pair, tuple) else pair
        label = _enum_value(getattr(item, "label", "text"))
        content_layer = _enum_value(getattr(item, "content_layer", "body"))
        role = _docling_role(label, content_layer)
        provs = list(getattr(item, "prov", None) or [])

        if label == "table" or hasattr(item, "data") and getattr(getattr(item, "data", None), "table_cells", None) is not None:
            rows = _docling_table_rows(item, document)
            if not rows:
                continue
            text = "\n".join("\t".join(row) for row in rows)
            if not provs:
                warnings.append("Docling table without page provenance was skipped.")
                continue
            prov = provs[0]
            page_no = int(getattr(prov, "page_no", 0) or 0)
            if page_no < 1:
                warnings.append("Docling table with invalid page provenance was skipped.")
                continue
            bbox = _docling_bbox(document, prov)
            if len({int(getattr(p, 'page_no', 0) or 0) for p in provs}) > 1:
                warnings.append(f"Multi-page table beginning on page {page_no} is stored as one table block; review table continuation.")
            blocks.append(
                SourceBlock(
                    id=_stable_block_id(paper_id, page_no, "docling-table", item_index, text),
                    page_number=page_no,
                    text=text,
                    bbox=bbox,
                    table_rows=rows,
                    role="table",
                    source_label=f"docling:{label}",
                )
            )
            continue

        text = str(getattr(item, "text", "") or getattr(item, "orig", "") or "")
        if not text.strip():
            continue
        if not provs:
            # Unlocated items are deliberately not evidence-bearing SourceBlocks.
            continue

        created = 0
        for prov_index, prov in enumerate(provs):
            page_no = int(getattr(prov, "page_no", 0) or 0)
            if page_no < 1:
                continue
            start, end = _charspan(getattr(prov, "charspan", None), len(text))
            piece = text[start:end].strip() if end > start else ""
            if not piece:
                if len(provs) == 1:
                    piece = text.strip()
                    start, end = 0, len(text)
                else:
                    continue
            bbox = _docling_bbox(document, prov)
            block_role = role
            if block_role == "body" and page_no == 1 and label in {"title"}:
                block_role = "front_matter"
            blocks.append(
                SourceBlock(
                    id=_stable_block_id(
                        paper_id,
                        page_no,
                        f"docling-{label}",
                        item_index * 100 + prov_index,
                        piece,
                    ),
                    page_number=page_no,
                    text=piece,
                    bbox=bbox,
                    role=block_role,
                    source_label=f"docling:{label}",
                )
            )
            created += 1
        if created == 0:
            warnings.append(f"Docling item '{label}' had unusable provenance and was skipped.")

    return blocks, warnings


def _docling_table_rows(item: object, document: object) -> list[list[str]]:
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None)
    if grid:
        rows: list[list[str]] = []
        for row in grid:
            values = [" ".join(str(getattr(cell, "text", cell) or "").split()) for cell in row]
            if any(values):
                rows.append(values)
        if rows:
            return rows
    try:
        frame = item.export_to_dataframe(doc=document)
        columns = [" ".join(str(value).split()) for value in list(frame.columns)]
        rows = [columns] if any(columns) else []
        for values in frame.itertuples(index=False, name=None):
            row = [" ".join(str(value if value is not None else "").split()) for value in values]
            if any(row):
                rows.append(row)
        return rows
    except Exception:
        return []


def _docling_bbox(document: object, prov: object) -> tuple[float, float, float, float] | None:
    bbox = getattr(prov, "bbox", None)
    if bbox is None:
        return None
    page_no = int(getattr(prov, "page_no", 0) or 0)
    try:
        page = getattr(document, "pages")[page_no]
        page_height = float(getattr(getattr(page, "size"), "height"))
        if hasattr(bbox, "to_top_left_origin"):
            bbox = bbox.to_top_left_origin(page_height=page_height)
    except Exception:
        pass
    try:
        return (
            float(getattr(bbox, "l")),
            float(getattr(bbox, "t")),
            float(getattr(bbox, "r")),
            float(getattr(bbox, "b")),
        )
    except Exception:
        return None


def _docling_role(label: str, content_layer: str) -> str:
    if content_layer == "furniture" or label in {"page_header", "page_footer"}:
        return "furniture"
    if label in {"section_header"}:
        return "heading"
    if label == "title":
        return "front_matter"
    if label == "footnote":
        return "footnote"
    if label == "table":
        return "table"
    return "body"


def _charspan(value: object, text_length: int) -> tuple[int, int]:
    try:
        start, end = value
        start = max(0, min(int(start), text_length))
        end = max(start, min(int(end), text_length))
        return start, end
    except Exception:
        return 0, text_length


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().casefold()


def _docling_installed() -> bool:
    try:
        return importlib.util.find_spec("docling") is not None
    except (ImportError, ValueError):
        return False


def _validate_pdf_path(pdf_path: Path) -> Path:
    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        raise DocumentParseError(f"PDF file does not exist: {path}")
    if path.suffix.lower() != ".pdf":
        raise DocumentParseError("Input must be a .pdf file")
    return path



def _extract_printed_page_number_blocks(path: Path, *, paper_id: str) -> list[SourceBlock]:
    """Extract bottom-of-page printed page numbers directly from the PDF text layer.

    This is intentionally independent of Docling. Many journal PDFs encode a number
    such as ``114`` as three separate glyphs / blocks, which can cause a layout model
    to miss early page footers. We cluster digit-only glyphs near the physical bottom
    edge and concatenate them left-to-right.
    """
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:
        return []
    try:
        return [
            block
            for page_index, page in enumerate(doc, start=1)
            if (block := _extract_printed_page_number_from_page(page, page_index, paper_id)) is not None
        ]
    finally:
        doc.close()


def _extract_printed_page_number_from_page(page: object, page_number: int, paper_id: str) -> SourceBlock | None:
    try:
        rect = page.rect
        page_height = float(rect.height)
        # Printed footers in academic journals are normally in the final ~10% of the page.
        clip = type(rect)(rect.x0, rect.y0 + page_height * 0.88, rect.x1, rect.y1)
        data = page.get_text("dict", clip=clip, sort=True)
    except Exception:
        return None

    digit_runs: list[tuple[float, float, float, float, str]] = []
    for raw_block in data.get("blocks", []):
        for line in raw_block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text", "")).strip()
                if not re.fullmatch(r"\d{1,4}", text):
                    continue
                try:
                    x0, y0, x1, y1 = map(float, span["bbox"])
                except Exception:
                    continue
                if y0 < page_height * 0.88:
                    continue
                digit_runs.append((x0, y0, x1, y1, text))

    if not digit_runs:
        return None

    # Cluster runs on the same baseline. The bottom-most cluster wins, and glyphs are
    # concatenated from left to right (e.g. separate ``1`` ``1`` ``4`` -> ``114``).
    clusters: list[list[tuple[float, float, float, float, str]]] = []
    for item in sorted(digit_runs, key=lambda value: ((value[1] + value[3]) / 2.0, value[0])):
        center_y = (item[1] + item[3]) / 2.0
        target = None
        for cluster in clusters:
            cluster_y = sum((x[1] + x[3]) / 2.0 for x in cluster) / len(cluster)
            if abs(center_y - cluster_y) <= 3.5:
                target = cluster
                break
        if target is None:
            target = []
            clusters.append(target)
        target.append(item)

    candidates=[]
    for cluster in clusters:
        ordered=sorted(cluster, key=lambda value:value[0])
        text="".join(item[4] for item in ordered)
        if not re.fullmatch(r"\d{1,4}", text):
            continue
        value=int(text)
        # Avoid picking tiny figure/table labels while still supporting journals whose
        # printed pages begin at 1.
        if not (1 <= value <= 9999):
            continue
        bbox=(min(x[0] for x in ordered), min(x[1] for x in ordered), max(x[2] for x in ordered), max(x[3] for x in ordered))
        bottom=max(x[3] for x in ordered)
        candidates.append((bottom, len(text), value, bbox, text))
    if not candidates:
        return None
    _, _, value, bbox, text=max(candidates, key=lambda item:(item[0], item[1]))
    normalized=str(value)
    return SourceBlock(
        id=_stable_block_id(paper_id, page_number, "printed-page-number", 0, normalized),
        page_number=page_number,
        text=normalized,
        bbox=bbox,
        role="furniture",
        source_label="sra:printed_page_number",
    )

def _overlap_ratio(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> float:
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    return intersection / area if area else 0.0


def _stable_block_id(paper_id: str, page: int, kind: str, index: int, text: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"sra:{paper_id}:{page}:{kind}:{index}:{text}"))


def _reading_order_key(block: SourceBlock) -> tuple[float, float, int]:
    if block.bbox is None:
        return (float(block.page_number), 0.0, 0)
    x0, y0, _, _ = block.bbox
    return (round(y0, 1), round(x0, 1), 0 if block.table_rows is None else 1)
