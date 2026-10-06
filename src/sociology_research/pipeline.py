"""Application workflow for importing and parsing one local paper."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import PaperRecord, ParsedDocument
from .parser import PyMuPDFDocumentParser
from .repository import ResearchRepository


class ImportPipeline:
    def __init__(self, data_dir: Path, repository: ResearchRepository, parser: PyMuPDFDocumentParser | None = None):
        self.data_dir = Path(data_dir)
        self.repository = repository
        self.parser = parser or PyMuPDFDocumentParser()

    def import_pdf(self, pdf_path: Path) -> tuple[PaperRecord, ParsedDocument, bool]:
        source = Path(pdf_path)
        if not source.exists() or not source.is_file():
            raise ValueError(f"Input file does not exist: {source}")
        if source.suffix.lower() != ".pdf":
            raise ValueError("Input must be a .pdf file")

        digest = self._sha256(source)
        existing = self.repository.get_by_sha256(digest)
        if existing:
            document = self.parser.parse(Path(existing.stored_path), paper_id=existing.id, sha256=digest)
            if existing.parser_version != document.parser_version:
                self.repository.refresh_parsed_document(existing.id, document)
                existing = self.repository.get_paper(existing.id)
            return existing, document, False

        paper_id = str(uuid4())
        destination = self.data_dir / "papers" / f"{digest}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)
        try:
            parsed = self.parser.parse(destination, paper_id=paper_id, sha256=digest)
            record = PaperRecord(
                id=paper_id,
                sha256=digest,
                original_name=source.name,
                stored_path=str(destination.resolve()),
                page_count=parsed.page_count,
                parse_status=parsed.status,
                parser_version=parsed.parser_version,
                warnings=parsed.warnings,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            saved = self.repository.save_import(record, parsed)
            return saved, parsed, True
        except Exception:
            if destination.exists() and self.repository.get_by_sha256(digest) is None:
                destination.unlink()
            raise

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
