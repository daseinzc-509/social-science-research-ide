"""Paper-library services built on the canonical single-paper import pipeline."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from .pipeline import ImportPipeline


@dataclass(slots=True)
class BatchImportResult:
    path: str
    status: str
    paper_id: str | None = None
    message: str = ""
    page_count: int | None = None
    parse_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class LibraryService:
    """Import a collection of PDFs without coupling import to model analysis."""

    def __init__(self, data_dir: Path, repository, *, parser=None, progress=None):
        self.import_pipeline = ImportPipeline(
            Path(data_dir), repository, parser=parser, progress=progress
        )

    @staticmethod
    def scan_pdfs(folder: str | Path, *, recursive: bool = True) -> list[Path]:
        root = Path(folder).expanduser()
        if not root.exists():
            return []
        if root.is_file():
            return [root] if root.suffix.lower() == ".pdf" else []
        iterator = root.rglob("*") if recursive else root.glob("*")
        return sorted(
            (item for item in iterator if item.is_file() and item.suffix.lower() == ".pdf"),
            key=lambda item: str(item).casefold(),
        )

    def batch_import(self, paths: Iterable[str | Path]) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.exists():
                results.append(BatchImportResult(str(path), "failed", message="file_not_found").to_dict())
                continue
            if not path.is_file():
                results.append(BatchImportResult(str(path), "skipped", message="not_a_file").to_dict())
                continue
            if path.suffix.lower() != ".pdf":
                results.append(BatchImportResult(str(path), "skipped", message="not_pdf").to_dict())
                continue
            try:
                record, parsed, created = self.import_pipeline.import_pdf(path)
                results.append(
                    BatchImportResult(
                        str(path),
                        "imported" if created else "duplicate",
                        paper_id=record.id,
                        page_count=record.page_count,
                        parse_status=parsed.status.value,
                        message="" if created else "identical_pdf_already_exists",
                    ).to_dict()
                )
            except Exception as exc:
                results.append(BatchImportResult(str(path), "failed", message=str(exc)).to_dict())
        return results
