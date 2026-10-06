"""PDF parsing boundary backed by PyMuPDF."""

from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .models import ParseStatus, ParsedDocument, SourceBlock


class DocumentParseError(ValueError):
    """Raised when a PDF cannot be safely parsed."""


class PyMuPDFDocumentParser:
    version = "pymupdf-tables-v2-reading-order"

    def parse(self, pdf_path: Path, *, paper_id: str, sha256: str) -> ParsedDocument:
        path = Path(pdf_path)
        if not path.exists() or not path.is_file():
            raise DocumentParseError(f"PDF file does not exist: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentParseError("Input must be a .pdf file")

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
                raise DocumentParseError("Encrypted PDFs are not supported in the first prototype")
            if document.page_count < 1:
                raise DocumentParseError("PDF contains no pages")

            blocks: list[SourceBlock] = []
            warnings: list[str] = []
            empty_pages: list[int] = []
            for page_index, page in enumerate(document, start=1):
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
                                page_number=page_index, text=table_text,
                                bbox=bbox, table_rows=rows,
                            )
                        )
                except Exception as exc:
                    warnings.append(f"Table extraction needs review on page {page_index}: {exc}")

                page_content: list[SourceBlock] = list(table_blocks)
                # PyMuPDF's sort=True is a cheap first-pass reading-order repair. It is not a
                # full layout model, but it prevents object-creation order from leaking directly
                # into the research evidence stream. Tables are merged back into the same visual
                # order after overlapping text blocks are removed.
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
                        )
                    )
                page_content.sort(key=_reading_order_key)
                blocks.extend(page_content)
                if extracted == 0:
                    empty_pages.append(page_index)

            if empty_pages:
                preview = ", ".join(map(str, empty_pages[:12]))
                suffix = "…" if len(empty_pages) > 12 else ""
                warnings.append(f"No selectable text found on page(s): {preview}{suffix}; OCR is not supported.")

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
    """Stable visual order for raw persisted blocks.

    The analyzer performs the higher-level paragraph reconstruction. This key merely keeps
    tables and text in the same page-local top-to-bottom order without discarding provenance.
    """
    if block.bbox is None:
        return (float(block.page_number), 0.0, 0)
    x0, y0, _, _ = block.bbox
    return (round(y0, 1), round(x0, 1), 0 if block.table_rows is None else 1)
