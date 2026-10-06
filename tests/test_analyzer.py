from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sociology_research.analyzer import PaperAnalysisPipeline
from sociology_research.config import Settings
from sociology_research.models import PaperRecord, ParseStatus, ParsedDocument, SourceBlock, VerificationStatus
from sociology_research.parser import PyMuPDFDocumentParser
from sociology_research.repository import ResearchRepository


class FakeClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.models: list[str] = []
        self.thinking: list[tuple[str | None, str | None]] = []

    def complete_json(
        self, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int,
        thinking: str | None = None, reasoning_effort: str | None = None,
    ) -> str:
        self.models.append(model)
        self.thinking.append((thinking, reasoning_effort))
        response = json.dumps(self.responses.pop(0), ensure_ascii=False)
        token_prefix = "E" if model == "lite-model" else "Q"
        match = re.search(rf"<{token_prefix}\d{{4}}>", user_prompt)
        if match is None and token_prefix == "Q":
            match = re.search(r'"evidence_id"\s*:\s*"(Q\d{4})"', user_prompt)
        if match:
            token = match.group(1) if match.lastindex else match.group(0).strip("<>")
            response = response.replace("__EVIDENCE_TOKEN__", token)
        return response


class TwoStagePipelineTests(unittest.TestCase):
    def test_lite_then_pro_are_saved_and_reused_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ResearchRepository(Path(temp_dir) / "research.sqlite3")
            paper_id = str(uuid4())
            source = SourceBlock(
                id="block-1",
                page_number=1,
                text="AI at Work. This is the abstract. The authors surveyed 120 workers. Results indicate improved autonomy.",
            )
            record = PaperRecord(
                id=paper_id,
                sha256="b" * 64,
                original_name="paper.pdf",
                stored_path=str(Path(temp_dir) / "paper.pdf"),
                page_count=1,
                parse_status=ParseStatus.SUCCESS,
                parser_version=PyMuPDFDocumentParser.version,
                created_at="2026-10-06T00:00:00+00:00",
            )
            repository.save_import(
                record,
                ParsedDocument(
                    paper_id=paper_id,
                    sha256=record.sha256,
                    source_path=record.stored_path,
                    page_count=1,
                    status=ParseStatus.SUCCESS,
                    parser_version=PyMuPDFDocumentParser.version,
                    blocks=[source],
                ),
            )
            lite = {
                "metadata_facts": [
                    {"field_name": "title", "value": "AI at Work", "evidence": [{"evidence_id": "__EVIDENCE_TOKEN__"}]},
                    {"field_name": "abstract", "value": "This is the abstract.", "evidence": [{"evidence_id": "__EVIDENCE_TOKEN__"}]},
                ],
                "basic_facts": [
                    {
                        "field_name": "method",
                        "statement": "The authors surveyed 120 workers.",
                        "provenance": "AUTHOR_STATED",
                        "verification": "SUPPORTED",
                        "evidence": [{"evidence_id": "__EVIDENCE_TOKEN__"}],
                    },
                ],
            }
            pro = {
                "analysis": [
                    {
                        "field_name": "method_reasonableness",
                        "statement": "Review the sampling procedure before judging representativeness.",
                        "provenance": "AI_INFERRED",
                        "verification": "NEEDS_REVIEW",
                        "evidence": [{"evidence_id": "__EVIDENCE_TOKEN__"}],
                    }
                ],
                "limitations": [],
                "reading_recommendation": {
                    "field_name": "reading_value",
                    "statement": "Potentially relevant to research on worker autonomy; compare with the project question.",
                    "provenance": "AI_INFERRED",
                    "verification": "NEEDS_REVIEW",
                    "evidence": [{"evidence_id": "__EVIDENCE_TOKEN__"}],
                },
            }
            fake = FakeClient([lite, pro])
            settings = Settings("test-key", "https://example.invalid/api/plan/v3", "lite-model", "pro-model")
            pipeline = PaperAnalysisPipeline(repository, settings=settings, client=fake)

            plan = pipeline.preview(paper_id)
            self.assertEqual(fake.models, [])
            first = pipeline.analyze(paper_id, research_context="worker autonomy")
            second = pipeline.analyze(paper_id, research_context="worker autonomy")

            self.assertEqual(fake.models, ["lite-model", "pro-model"])
            self.assertEqual(fake.thinking, [("disabled", None), ("enabled", "low")])
            self.assertEqual(plan["lite_requests"], 1)
            self.assertEqual(first.metadata.title, "AI at Work")
            self.assertEqual(len(first.basic_facts), 1)
            self.assertEqual(first.analysis[0].verification, VerificationStatus.NEEDS_REVIEW)
            self.assertEqual(second.paper_id, paper_id)
            self.assertEqual(repository.get_card(paper_id).metadata.abstract, "This is the abstract.")


if __name__ == "__main__":
    unittest.main()
