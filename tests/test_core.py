from __future__ import annotations

import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError

from sociology_research.models import (
    ExtractedClaim,
    PaperRecord,
    ParseStatus,
    ParsedDocument,
    Provenance,
    SourceBlock,
    VerificationStatus,
)
from sociology_research.config import Settings
from sociology_research.llm_client import ModelRequestError, OpenAICompatibleClient
from sociology_research.parser import DocumentParseError, PyMuPDFDocumentParser
from sociology_research.repository import ResearchRepository


class DefensiveModelTests(unittest.TestCase):
    def test_source_block_rejects_invalid_page_and_blank_text(self) -> None:
        with self.assertRaises(ValidationError):
            SourceBlock(id="x", page_number=0, text="text")
        with self.assertRaises(ValidationError):
            SourceBlock(id="x", page_number=1, text="  ")

    def test_supported_claim_requires_traceable_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            ExtractedClaim(
                field_name="finding",
                statement="A claim",
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
            )

    def test_model_settings_report_missing_values_without_exposing_secrets(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SRA_API_KEY": "",
                "SRA_API_BASE_URL": "",
                "SRA_LITE_MODEL": "",
                "SRA_PRO_MODEL": "",
            },
        ):
            with self.assertRaisesRegex(ValueError, "SRA_API_KEY.*SRA_API_BASE_URL.*SRA_LITE_MODEL.*SRA_PRO_MODEL"):
                Settings.from_environment().require_model_configuration()

    def test_truncated_model_response_is_reported_without_retry(self) -> None:
        response = BytesIO(
            b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}'
        )
        with patch("sociology_research.llm_client.urlopen", return_value=response):
            client = OpenAICompatibleClient(api_key="test-key", base_url="https://example.invalid/api/plan/v3")
            with self.assertRaisesRegex(ModelRequestError, "truncated"):
                client.complete_json(
                    model="test-model", system_prompt="system", user_prompt="user", max_tokens=8,
                )


class RepositoryTests(unittest.TestCase):
    def test_imported_paper_round_trips_through_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ResearchRepository(Path(temp_dir) / "research.sqlite3")
            paper_id = str(uuid4())
            digest = "a" * 64
            record = PaperRecord(
                id=paper_id,
                sha256=digest,
                original_name="paper.pdf",
                stored_path=str(Path(temp_dir) / "paper.pdf"),
                page_count=1,
                parse_status=ParseStatus.SUCCESS,
                created_at="2026-10-06T00:00:00+00:00",
            )
            parsed = ParsedDocument(
                paper_id=paper_id,
                sha256=digest,
                source_path=record.stored_path,
                page_count=1,
                status=ParseStatus.SUCCESS,
                blocks=[SourceBlock(id="block-1", page_number=1, text="Evidence text")],
            )
            repository.save_import(record, parsed)
            loaded = repository.get_paper(paper_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.sha256, digest)
            self.assertEqual(repository.get_by_sha256(digest).id, paper_id)


class ParserBoundaryTests(unittest.TestCase):
    def test_missing_file_fails_before_parser_dependency_is_needed(self) -> None:
        parser = PyMuPDFDocumentParser()
        with self.assertRaisesRegex(DocumentParseError, "does not exist"):
            parser.parse(Path("missing.pdf"), paper_id="paper", sha256="hash")

    def test_non_pdf_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "notes.txt"
            path.write_text("not a PDF", encoding="utf-8")
            with self.assertRaisesRegex(DocumentParseError, "must be a .pdf"):
                PyMuPDFDocumentParser().parse(path, paper_id="paper", sha256="hash")

    def test_textless_page_is_marked_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scan.pdf"
            document = fitz.open()
            document.new_page()
            document.save(path)
            document.close()

            parsed = PyMuPDFDocumentParser().parse(path, paper_id="paper", sha256="hash")
            self.assertEqual(parsed.status, ParseStatus.NEEDS_REVIEW)
            self.assertTrue(parsed.warnings)


if __name__ == "__main__":
    unittest.main()
