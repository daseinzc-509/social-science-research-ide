"""Docling-only PDF parsing with conservative OCR recovery and source provenance.

Docling owns document layout, text, OCR and tables. The parser never calls fitz
or PyMuPDF. Suspicious Docling output triggers a bounded full-page OCR retry;
a replacement must improve quality without losing previously recovered pages.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from .models import FigureRegion, ParseStatus, ParsedDocument, SourceBlock, TableCellSpan


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
    """Require Docling in development and in frozen desktop builds.

    ``auto`` remains a legacy alias, so older user configuration is compatible.
    Other parsers fail explicitly rather than silently changing extraction quality.
    """
    requested = os.environ.get("SRA_PDF_PARSER", "docling").strip().casefold() or "docling"
    if requested not in {"auto", "docling"}:
        raise DocumentParseError(
            "SRA_PDF_PARSER must be 'docling' (legacy 'auto' also works); "
            "the PyMuPDF fallback has been removed."
        )
    if not _docling_installed():
        raise DocumentParseError(
            "Docling is required for SRA PDF import, but is not installed. "
            "Install the project's Docling-only dependencies."
        )
    return DoclingDocumentParser()


class DoclingDocumentParser:
    """Semantic PDF parser backed exclusively by Docling."""

    def __init__(self) -> None:
        try:
            version = importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            version = "missing"
        # Formula enrichment is opt-in: it may require additional model weights.
        # UI preferences are persisted in the per-user config store. A process
        # environment variable can override them for reproducible CI/dev runs.
        value = os.environ.get("SRA_DOCLING_FORMULA_ENRICHMENT")
        if value is None:
            value = os.environ.get("SRA_FORMULA_ENRICHMENT")
        if value is None:
            from .user_storage import read_user_preferences
            value = read_user_preferences().get("SRA_FORMULA_ENRICHMENT", "0")
        self.formula_enabled = str(value).strip().casefold() in {"1", "true", "yes", "on"}
        option = "formula" if self.formula_enabled else "default"
        # Parser version changes intentionally invalidate stale evidence caches on reparse.
        self.version = f"docling-only-v2-table-spans-{option}:{version}"

    def parse(
        self,
        pdf_path: Path,
        *,
        paper_id: str,
        sha256: str,
        progress: Callable[[str], None] | None = None,
    ) -> ParsedDocument:
        path = _validate_pdf_path(pdf_path)
        if not _docling_installed():
            raise DocumentParseError("Docling is required but is not installed.")

        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except Exception as exc:
            raise DocumentParseError(f"Could not import Docling: {exc}") from exc

        def convert(*, full_page: bool, formula: bool):
            options = PdfPipelineOptions()
            if formula:
                if not hasattr(options, "do_formula_enrichment"):
                    raise DocumentParseError(
                        "Installed Docling does not support formula enrichment."
                    )
                options.do_formula_enrichment = True
            # Standard OCR avoids erasing trustworthy embedded text. Do not turn
            # OCR off blindly on a born-digital document with mixed scanned pages.
            options.do_ocr = True
            options.do_table_structure = True
            if hasattr(options, "generate_page_images"):
                options.generate_page_images = False
            if hasattr(options, "generate_picture_images"):
                options.generate_picture_images = False
            if full_page and not _enable_full_page_ocr(options):
                raise DocumentParseError(
                    "This Docling version cannot enable full-page OCR; "
                    "the original extraction is preserved for manual review."
                )
            try:
                from docling.datamodel.pipeline_options import TableFormerMode
                options.table_structure_options.mode = TableFormerMode.ACCURATE
            except (AttributeError, ImportError, ValueError):
                pass
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            label = "full-page OCR" if full_page else "layout / standard OCR"
            _emit(progress, f"Docling: converting with {label}...")
            result = _convert_with_heartbeat(converter, path, progress=progress)
            document = result.document
            blocks, warnings = _blocks_from_docling_document(document, paper_id=paper_id)
            count = len(getattr(document, "pages", {}) or {})
            count = count or max((block.page_number for block in blocks), default=0)
            if count < 1:
                raise DocumentParseError("Docling produced no pages")
            return result, document, blocks, warnings, count

        active_formula = self.formula_enabled
        try:
            result, document, blocks, warnings, page_count = convert(
                full_page=False, formula=active_formula
            )
        except Exception as exc:
            if not active_formula:
                if isinstance(exc, DocumentParseError):
                    raise
                raise DocumentParseError(f"Docling could not parse PDF: {exc}") from exc
            # Failure of optional enrichment must not prevent ordinary reading.
            _emit(progress, "Docling formula enrichment unavailable; falling back to ordinary PDF parsing.")
            try:
                result, document, blocks, warnings, page_count = convert(
                    full_page=False, formula=False
                )
            except Exception as fallback_exc:
                raise DocumentParseError(
                    f"Docling could not parse PDF: {fallback_exc}"
                ) from fallback_exc
            active_formula = False
            warnings.append(
                "Optional formula enrichment failed; ordinary Docling parsing retained. "
                "Check formulas against the PDF before citing."
            )

        first_quality = _docling_text_quality(blocks, page_count)
        override_full_ocr = os.environ.get("SRA_DOCLING_FULL_PAGE_OCR", "").strip().casefold() in {
            "1", "true", "yes", "on"
        }
        if first_quality["suspicious"] or first_quality["empty"] or override_full_ocr:
            reason = (
                f"{len(first_quality['suspicious'])} suspicious text page(s), "
                f"{len(first_quality['empty'])} empty page(s)"
            )
            _emit(progress, f"Docling: {reason}; evaluating full-page OCR recovery...")
            try:
                candidate_result, candidate_doc, candidate_blocks, candidate_warnings, candidate_pages = convert(
                    full_page=True, formula=active_formula
                )
                candidate_quality = _docling_text_quality(candidate_blocks, candidate_pages)
                if _prefer_ocr_candidate(
                    first_quality, candidate_quality,
                    same_pages=candidate_pages == page_count,
                    forced=override_full_ocr,
                ):
                    result = candidate_result
                    document = candidate_doc
                    blocks = candidate_blocks
                    warnings = candidate_warnings
                    page_count = candidate_pages
                    first_quality = candidate_quality
                    _emit(progress, "Docling: full-page OCR improved extracted text; using reviewed result.")
                else:
                    warnings.append(
                        "Full-page OCR did not safely improve extracted text; "
                        "original Docling output retained for manual review."
                    )
                    _emit(progress, "Docling: retaining original extraction instead of losing evidence.")
            except Exception as exc:
                # A second, optional OCR pass must never discard a usable first pass.
                warnings.append(f"Full-page OCR retry unavailable: {type(exc).__name__}; review original extraction.")
                _emit(progress, "Docling: full-page OCR unavailable; original extraction retained.")

        if self.formula_enabled:
            formula_labels = {"docling:formula", "docling:equation"}
            count_formulas = sum(
                (block.source_label or "").casefold() in formula_labels
                for block in blocks
            )
            if active_formula:
                _emit(progress,
                      f"Docling: formula enrichment requested; {count_formulas} "
                      "located formula block(s) emitted.")
                if count_formulas == 0:
                    _emit(progress,
                          "Docling did not emit any located formula text; check "
                          "whether the PDF contains recognizable formulas or "
                          "whether this Docling/model build supports them.")
            else:
                _emit(progress,
                      "Formula enrichment did not complete; ordinary Docling "
                      "parse was retained. See parser warnings.")
        figures = _docling_figure_regions(document, paper_id=paper_id)
        blocks.extend(_docling_printed_page_number_blocks(document, blocks, paper_id=paper_id))
        # Preserve Docling's semantic item reading order. Metadata page-number
        # furniture is appended (and excluded from evidence chunking by role).
        quality = _docling_text_quality(blocks, page_count)
        if quality["suspicious"]:
            warnings.append(
                "Potentially corrupted Docling text on PDF page(s): "
                + ", ".join(map(str, quality["suspicious"][:12]))
                + ". Check the original PDF before trusting these quotations."
            )
        if quality["empty"]:
            warnings.append(
                "Docling found no body text on PDF page(s): "
                + ", ".join(map(str, quality["empty"][:12]))
                + ". Blank image-only pages may be legitimate; verify important content."
            )
        status_value = str(getattr(result, "status", "")).casefold()
        # PARTIAL_SUCCESS is not a complete successful parse: surface it for
        # researcher review instead of accepting it silently.
        if status_value and ("success" not in status_value or "partial" in status_value):
            warnings.append(f"Docling conversion status needs review: {getattr(result, 'status', status_value)}")
        status = ParseStatus.NEEDS_REVIEW if warnings else ParseStatus.SUCCESS
        _emit(progress, f"Docling: parsed {len(blocks)} source blocks across {page_count} pages.")
        return ParsedDocument(
            paper_id=paper_id,
            sha256=sha256,
            source_path=str(path.resolve()),
            page_count=page_count,
            status=status,
            parser_version=self.version,
            warnings=warnings,
            blocks=blocks,
            figures=figures,
        )


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _docling_text_quality(blocks: list[SourceBlock], page_count: int) -> dict[str, object]:
    """Inspect text yielded by Docling; never fabricate page text or citations."""
    by_page: dict[int, list[str]] = {}
    for block in blocks:
        if block.role != "furniture" and block.text.strip():
            by_page.setdefault(block.page_number, []).append(block.text)
    texts = {page: "\n".join(parts) for page, parts in by_page.items()}
    return {
        "suspicious": [
            page for page, text in texts.items() if _looks_like_garbled_text(text)
        ],
        "empty": [
            page for page in range(1, page_count + 1)
            if page not in texts
        ],
        "pages_with_text": len(texts),
        "chars": sum(len("".join(t.split())) for t in texts.values()),
    }


def _prefer_ocr_candidate(
    old: dict[str, object],
    new: dict[str, object],
    *,
    same_pages: bool,
    forced: bool = False,
) -> bool:
    """Never replace a better or more complete document with an OCR regression."""
    if not same_pages or new["suspicious"]:
        # Exception: reducing multiple bad pages to fewer is useful, but not
        # if all other page coverage deteriorates.
        if not same_pages or len(new["suspicious"]) >= len(old["suspicious"]):
            return False
    if new["pages_with_text"] < old["pages_with_text"]:
        return False
    if new["chars"] < old["chars"] * 0.60:
        return False
    less_corrupt = len(new["suspicious"]) < len(old["suspicious"])
    more_complete = new["pages_with_text"] > old["pages_with_text"]
    if less_corrupt or more_complete:
        return True
    # An explicit forced attempt is permitted only when the output is no worse
    # by the detectable signals; normal runs never replace an equal result.
    return bool(
        forced and not new["suspicious"]
        and new["chars"] >= old["chars"]
    )


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _is_cjk_like(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _garbled_text_score(text: str) -> int:
    """Return a conservative corruption score for an embedded PDF text layer.

    The important case is a visually normal CJK PDF whose ToUnicode/font mapping
    yields valid-but-nonsensical ASCII fragments (for example stray ``#``, ``_``
    and one-letter Latin runs) instead of the visible Chinese characters.  Simple
    character-count checks treat such pages as healthy, so we score several
    independent signals and only flag pages when they agree.
    """

    normalized = unicodedata.normalize("NFKC", text or "")
    compact = "".join(char for char in normalized if not char.isspace())
    if not compact:
        return 0

    score = 0
    length = len(compact)
    hard_bad = 0
    for char in compact:
        category = unicodedata.category(char)
        if char == "\ufffd" or category in {"Co", "Cs"}:
            hard_bad += 1
        elif category == "Cc":
            hard_bad += 1
    if hard_bad >= 2 or hard_bad / length >= 0.005:
        score += 7
    elif hard_bad == 1:
        score += 2

    cjk_count = sum(1 for char in compact if _is_cjk_like(char))
    cjk_ratio = cjk_count / length
    suspicious_symbols = sum(char in "#^_`~|■□�" for char in compact)
    if cjk_ratio >= 0.20:
        symbol_threshold = max(3, length // 220)
        if suspicious_symbols >= symbol_threshold:
            score += 5
        elif suspicious_symbols >= 2:
            score += 1

        single_ascii = len(
            re.findall(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", normalized)
        )
        if single_ascii >= max(8, length // 90):
            score += 4

        # Broken CJK font maps often inject short Latin fragments directly between
        # Han characters.  Legitimate acronyms (AI, LLM, DAG) are normally sparse,
        # so require many transitions before treating them as a corruption signal.
        mixed_fragments = len(
            re.findall(
                r"(?<=[\u3400-\u9fff])[#^_`~|A-Za-z]{1,2}(?=[\u3400-\u9fff])",
                compact,
            )
        )
        if mixed_fragments >= max(6, length // 120):
            score += 4

    return score


def _looks_like_garbled_text(text: str) -> bool:
    compact_length = len(re.sub(r"\s+", "", text or ""))
    if compact_length < 40:
        return False
    return _garbled_text_score(text) >= 7


def _enable_full_page_ocr(options: object) -> bool:
    """Switch the installed Docling OCR options to full-page replacement mode."""

    ocr_options = getattr(options, "ocr_options", None)
    if ocr_options is None:
        return False
    try:
        from docling.datamodel.pipeline_options import OcrMode

        ocr_options.mode = OcrMode.FULL_PAGE
        return True
    except Exception:
        # Docling 2.x builds also expose a compatibility property.  Keep this
        # fallback so the parser remains usable across the supported minor range.
        try:
            ocr_options.force_full_page_ocr = True
            return True
        except Exception:
            return False


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
            cells, cell_warning = _docling_table_cells(item, rows)
            if cell_warning:
                warnings.append(f"Table {item_index}: {cell_warning}")
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
                    table_cells=cells,
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
    """Keep the entire Docling dense grid, including structural blank rows.

    Dropping a blank row shifts the coordinates of any row-spanning cells.
    The dense grid is still useful for raw export and older Paper Cards.
    """
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None)
    if grid is not None:
        rows = [
            [" ".join(str(getattr(cell, "text", cell) or "").split()) for cell in row]
            for row in grid
        ]
        if any(any(value for value in row) for row in rows):
            return rows
    try:
        frame = item.export_to_dataframe(doc=document)
        columns = [" ".join(str(value).split()) for value in list(frame.columns)]
        rows = [columns] if any(columns) else []
        for values in frame.itertuples(index=False, name=None):
            rows.append([
                " ".join(str(value if value is not None else "").split()) for value in values
            ])
        return rows if any(any(value for value in row) for row in rows) else []
    except Exception:
        return []


def _docling_table_cells(
    item: object, rows: list[list[str]]
) -> tuple[list[TableCellSpan] | None, str | None]:
    """Read first-class cell coordinates, never infer spans from repeated text.

    A malformed span layout is rejected as a *whole*. The existing dense grid
    remains intact and can still be displayed without suggesting false merges.
    """
    data = getattr(item, "data", None)
    entries = getattr(data, "table_cells", None)
    if not entries:
        return None, None
    height = len(rows)
    width = max((len(row) for row in rows), default=0)
    if height < 1 or width < 1:
        return None, "Docling supplied cell spans without a usable grid; showing flat rows."
    occupied: set[tuple[int, int]] = set()
    converted: list[TableCellSpan] = []
    try:
        for cell in entries:
            row = int(getattr(cell, "start_row_offset_idx"))
            col = int(getattr(cell, "start_col_offset_idx"))
            row_end = int(getattr(cell, "end_row_offset_idx", row + 1))
            col_end = int(getattr(cell, "end_col_offset_idx", col + 1))
            row_span = row_end - row
            col_span = col_end - col
            if row < 0 or col < 0 or row_span < 1 or col_span < 1:
                raise ValueError("negative index or nonpositive span")
            if row + row_span > height or col + col_span > width:
                raise ValueError("cell span falls outside the dense table grid")
            area = {
                (r, c)
                for r in range(row, row + row_span)
                for c in range(col, col + col_span)
            }
            if occupied.intersection(area):
                raise ValueError("overlapping cell spans")
            occupied.update(area)
            converted.append(TableCellSpan(
                row=row,
                col=col,
                row_span=row_span,
                col_span=col_span,
                text=str(getattr(cell, "text", "") or ""),
                column_header=bool(getattr(cell, "column_header", False)),
                row_header=bool(getattr(cell, "row_header", False)),
            ))
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        return None, f"Invalid Docling span metadata ({exc}); raw table grid preserved."
    return converted or None, None


def _docling_figure_regions(document: object, *, paper_id: str) -> list[FigureRegion]:
    """Locate pictures without treating AI descriptions as observed evidence.

    This is best-effort metadata only: malformed figure captions/refs must not
    block otherwise successful PDF import. No pixels or statistics are invented.
    """
    try:
        iterator = document.iterate_items()
    except Exception:
        return []
    found: list[FigureRegion] = []
    try:
        for index, pair in enumerate(iterator):
            item = pair[0] if isinstance(pair, tuple) else pair
            label = _enum_value(getattr(item, "label", ""))
            if label not in {"picture", "figure", "chart"}:
                continue
            captions = []
            for ref in getattr(item, "captions", []) or []:
                text_item = None
                try:
                    text_item = document.resolve_ref(ref)
                except Exception:
                    # Caption absence is not a reason to discard the picture.
                    pass
                text = str(getattr(text_item, "text", "") or "").strip()
                if text:
                    captions.append(text)
            caption = " ".join(captions) or str(getattr(item, "text", "") or "").strip()
            for prov_index, prov in enumerate(getattr(item, "prov", None) or []):
                try:
                    page = int(getattr(prov, "page_no", 0) or 0)
                    if page < 1:
                        continue
                    bbox = _docling_bbox(document, prov)
                    if bbox is None:
                        continue
                    found.append(FigureRegion(
                        id=_stable_block_id(
                            paper_id, page, f"docling-{label}", index * 100 + prov_index, caption
                        ),
                        page_number=page,
                        bbox=bbox,
                        caption=caption,
                        source_label=f"docling:{label}",
                    ))
                except (TypeError, ValueError, OverflowError):
                    continue
    except Exception:
        # Best-effort metadata: never lose a successfully parsed PDF because
        # one Docling picture object had broken optional provenance.
        return found
    return found


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



def _docling_printed_page_number_blocks(
    document: object, blocks: list[SourceBlock], *, paper_id: str,
) -> list[SourceBlock]:
    """Reconstruct printed page numbers *only* from Docling page-footer items.

    PDFs can split 114 into three item glyphs. Use Docling provenance/bboxes,
    cluster close digits on one baseline and never invent a page number when
    no footer is actually available. PDF page_number remains authoritative.
    """
    pages = getattr(document, "pages", {}) or {}
    items_by_page: dict[int, list[SourceBlock]] = {}
    for block in blocks:
        if (block.source_label or "").casefold() != "docling:page_footer":
            continue
        if block.bbox is None or not re.fullmatch(r"\d{1,4}", block.text.strip()):
            continue
        try:
            page_height = float(pages[block.page_number].size.height)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            continue
        if block.bbox[1] >= page_height * 0.86:
            items_by_page.setdefault(block.page_number, []).append(block)

    found: list[SourceBlock] = []
    for page_number, candidates in items_by_page.items():
        baseline_groups: list[list[SourceBlock]] = []
        for item in sorted(candidates, key=lambda x: ((x.bbox[1] + x.bbox[3]) / 2, x.bbox[0])):
            baseline = (item.bbox[1] + item.bbox[3]) / 2
            group = next((
                g for g in baseline_groups if abs(
                    baseline - sum((x.bbox[1] + x.bbox[3]) / 2 for x in g) / len(g)
                ) <= 3.5
            ), None)
            if group is None:
                group = []
                baseline_groups.append(group)
            group.append(item)

        valid: list[tuple[float, str, tuple[float, float, float, float]]] = []
        for group in baseline_groups:
            runs: list[list[SourceBlock]] = []
            for item in sorted(group, key=lambda x: x.bbox[0]):
                if not runs or item.bbox[0] - runs[-1][-1].bbox[2] > 12:
                    runs.append([])
                runs[-1].append(item)
            for run in runs:
                text = "".join(x.text.strip() for x in run)
                if not re.fullmatch(r"\d{1,4}", text) or not 1 <= int(text) <= 9999:
                    continue
                bbox = (
                    min(x.bbox[0] for x in run),
                    min(x.bbox[1] for x in run),
                    max(x.bbox[2] for x in run),
                    max(x.bbox[3] for x in run),
                )
                valid.append((bbox[3], str(int(text)), bbox))

        if not valid:
            continue
        _bottom, label, bbox = max(valid, key=lambda v: (v[0], len(v[1])))
        found.append(SourceBlock(
            id=_stable_block_id(paper_id, page_number, "printed-page-number", 0, label),
            page_number=page_number,
            text=label,
            bbox=bbox,
            role="furniture",
            source_label="sra:printed_page_number",
        ))
    return found


def _stable_block_id(paper_id: str, page: int, kind: str, index: int, text: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"sra:{paper_id}:{page}:{kind}:{index}:{text}"))


def _reading_order_key(block: SourceBlock) -> tuple[float, float, int]:
    if block.bbox is None:
        return (float(block.page_number), 0.0, 0)
    x0, y0, _, _ = block.bbox
    return (round(y0, 1), round(x0, 1), 0 if block.table_rows is None else 1)
