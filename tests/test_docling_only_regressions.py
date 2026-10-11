"""SRA regression tests for Docling-only migration and robust batch imports.

These tests do not call an LLM, download model weights or use private papers.
Docling conversion is stubbed except for the separate frozen PDFium smoke test.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
import time
import tomllib
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from sociology_research import parser
from sociology_research.models import (
    LLMClaim, LLMMetadataFact, ParseStatus, ParsedDocument, SourceBlock,
)
from sociology_research.repository import ResearchRepository
from sociology_research.pipeline import ImportPipeline
from sociology_research.services.papers import PaperService
from sociology_research.services.jobs import JobService


ROOT = Path(__file__).resolve().parents[1]


def refs(count: int) -> list[dict]:
    return [{"evidence_id": f"E{index:04d}"} for index in range(count)]


@pytest.mark.parametrize("count", [1, 3, 4, 16])
def test_lite_four_evidence_and_boundary_acceptance(count):
    claim = LLMClaim.model_validate({
        "field_name": "major_finding",
        "statement": "Results show a reported association.",
        "provenance": "AUTHOR_STATED",
        "verification": "SUPPORTED",
        "evidence": refs(count),
    })
    assert [item.evidence_id for item in claim.evidence] == [
        f"E{index:04d}" for index in range(count)
    ]


def test_lite_rejects_out_of_range_and_ungrounded():
    with pytest.raises(ValidationError):
        LLMClaim.model_validate({
            "field_name": "major_finding",
            "statement": "A claim.",
            "provenance": "AUTHOR_STATED",
            "verification": "SUPPORTED",
            "evidence": refs(17),
        })
    with pytest.raises(ValidationError, match="SUPPORTED"):
        LLMClaim.model_validate({
            "field_name": "major_finding",
            "statement": "A claim.",
            "provenance": "AUTHOR_STATED",
            "verification": "SUPPORTED",
            "evidence": [],
        })


@pytest.mark.parametrize("count", [1, 4, 16])
def test_metadata_evidence_kept_without_discarding(count):
    fact = LLMMetadataFact.model_validate({
        "field_name": "title", "value": "An academic paper", "evidence": refs(count)
    })
    assert len(fact.evidence) == count


@pytest.mark.parametrize("count", [0, 17])
def test_metadata_evidence_bounds_remain_checked(count):
    with pytest.raises(ValidationError):
        LLMMetadataFact.model_validate({
            "field_name": "title", "value": "A paper", "evidence": refs(count)
        })


def test_only_docling_is_selected(monkeypatch):
    monkeypatch.setenv("SRA_PDF_PARSER", "pymupdf")
    with pytest.raises(parser.DocumentParseError, match="removed"):
        parser.create_default_parser()
    monkeypatch.setenv("SRA_PDF_PARSER", "docling")
    monkeypatch.setattr(parser, "_docling_installed", lambda: False)
    with pytest.raises(parser.DocumentParseError, match="required"):
        parser.create_default_parser()
    monkeypatch.setattr(parser, "_docling_installed", lambda: True)
    assert isinstance(parser.create_default_parser(), parser.DoclingDocumentParser)


def block(number, text, role="body", label="docling:text", bbox=None):
    return SourceBlock(
        id=f"block-{number}", page_number=number, text=text,
        role=role, source_label=label, bbox=bbox,
    )


def test_quality_guard_does_not_replace_more_complete_text():
    clean = [block(1, "This is an ordinary paragraph with useful extracted text."),
             block(2, "Another page with important methods and evidence.")]
    shorter = [block(1, "brief text")]
    current = parser._docling_text_quality(clean, 2)
    candidate = parser._docling_text_quality(shorter, 2)
    assert current["pages_with_text"] == 2
    assert candidate["empty"] == [2]
    assert not parser._prefer_ocr_candidate(current, candidate, same_pages=True)
    assert not parser._prefer_ocr_candidate(current, candidate, same_pages=False)


def test_full_ocr_selected_only_on_quality_gain():
    garbled = [block(1, "中文#" * 35 + "__###___" * 6)]
    fixed = [block(1, "这是一篇包含研究问题、抽样策略和方法解释的中文学术论文。" * 5)]
    before = parser._docling_text_quality(garbled, 1)
    after = parser._docling_text_quality(fixed, 1)
    assert before["suspicious"], before
    assert not after["suspicious"]
    assert parser._prefer_ocr_candidate(before, after, same_pages=True)
    assert not parser._prefer_ocr_candidate(
        after, before, same_pages=True
    )


def test_printed_page_number_from_docling_footer_only():
    page = types.SimpleNamespace(size=types.SimpleNamespace(height=1000))
    document = types.SimpleNamespace(pages={1: page})
    items = [
        block(1, "1", "furniture", "docling:page_footer", (100, 945, 105, 955)),
        block(1, "1", "furniture", "docling:page_footer", (106, 945, 111, 955)),
        block(1, "4", "furniture", "docling:page_footer", (112, 945, 117, 955)),
        block(1, "25", "body", "docling:text", (90, 955, 120, 969)),
        block(1, "2024", "furniture", "docling:page_header", (0, 6, 45, 16)),
    ]
    generated = parser._docling_printed_page_number_blocks(document, items, paper_id="paper")
    assert len(generated) == 1
    assert generated[0].text == "114"
    assert generated[0].source_label == "sra:printed_page_number"
    assert generated[0].bbox == (100., 945., 117., 955.)


def test_missing_docling_footer_does_not_fabricate_page_number():
    page = types.SimpleNamespace(size=types.SimpleNamespace(height=1000))
    document = types.SimpleNamespace(pages={1: page})
    items = [block(1, "214", "body", "docling:text", (50, 950, 80, 962))]
    assert parser._docling_printed_page_number_blocks(document, items, paper_id="paper") == []


def test_batch_import_preserves_original_filenames_and_isolates_failures(tmp_path):
    seen = []
    class FakePipeline:
        def __init__(self, *_args, **_kwargs): pass
        def import_pdf(self, path):
            seen.append((path.name, path.parent.name))
            if path.name == "broken.pdf":
                raise RuntimeError("simulated damaged PDF")
            record = types.SimpleNamespace(id=f"id-{len(seen)}", page_count=1,
                                           parse_status=ParseStatus.SUCCESS)
            parsed = types.SimpleNamespace(warnings=[])
            return record, parsed, True

    jobs = JobService()
    service = PaperService(tmp_path, object(), jobs, import_pipeline_factory=FakePipeline)
    job_id = service.start_batch_import([
        ("同名.pdf", b"%PDF-one"),
        ("同名.pdf", b"%PDF-two"),
        ("broken.pdf", b"%PDF-three"),
        ("final.pdf", b"%PDF-four"),
    ])
    for _ in range(400):
        state = jobs.get(job_id)
        if state and state["status"] in {"done", "error"}:
            break
        time.sleep(0.01)
    assert state["status"] == "done", state
    assert state["result"]["imported"] == 3
    assert state["result"]["failed"] == 1
    assert "simulated damaged" in state["result"]["results"][2]["error"]
    assert [name for name, _ in seen] == [
        "同名.pdf", "同名.pdf", "broken.pdf", "final.pdf"
    ]
    assert len({folder for _, folder in seen}) == 4
    assert all(len(folder) == 4 and folder.isdigit() for _, folder in seen)


def test_batch_rejects_empty_and_continues(tmp_path):
    class FakePipeline:
        def __init__(self, *_args, **_kwargs): pass
        def import_pdf(self, path):
            rec=types.SimpleNamespace(id="x",page_count=1,parse_status=ParseStatus.SUCCESS)
            return rec, types.SimpleNamespace(warnings=[]), True
    jobs=JobService()
    service=PaperService(tmp_path, object(), jobs, import_pipeline_factory=FakePipeline)
    job_id=service.start_batch_import([
        ("empty.pdf", b""),
        ("valid.pdf", b"%PDF-valid"),
    ])
    for _ in range(400):
        result=jobs.get(job_id)
        if result and result["status"] in {"done","error"}:
            break
        time.sleep(0.01)
    assert result["status"]=="done"
    assert result["result"]["imported"]==1
    assert result["result"]["failed"]==1
    assert result["result"]["results"][-1]["status"]=="rejected"


def test_duplicate_import_reuses_existing_parsing(tmp_path):
    data=b"%PDF-1.4\n% fake bytes for parser stub"
    source=tmp_path/"same.pdf"
    source.write_bytes(data)
    calls=[]
    class FakeParser:
        version="fake-parser-v2"
        def parse(self, path, *, paper_id, sha256, progress=None):
            calls.append(path)
            return ParsedDocument(
                paper_id=paper_id,sha256=sha256,source_path=str(path),
                page_count=1,status=ParseStatus.SUCCESS,
                parser_version=self.version,
                blocks=[SourceBlock(
                    id=f"source-{paper_id}",page_number=1,
                    text="The method section states the identification strategy.",
                    role="body",
                )]
            )
    repository=ResearchRepository(tmp_path/"research.sqlite3")
    pipeline=ImportPipeline(tmp_path,repository,parser=FakeParser())
    first, parsed1, created=pipeline.import_pdf(source)
    second, parsed2, created2=pipeline.import_pdf(source)
    assert created and not created2
    assert second.id==first.id
    assert len(calls)==1
    assert parsed1.blocks==parsed2.blocks


def test_dependency_and_frozen_configuration():
    data=tomllib.loads((ROOT/"pyproject.toml").read_text(encoding="utf-8"))
    deps=data["project"]["dependencies"]
    assert "docling==2.134.0" in deps
    assert not any("pymupdf" in d.casefold() for d in deps)
    assert "api" in data["project"]["optional-dependencies"]
    build=(ROOT/"scripts/build_backend.py").read_text(encoding="utf-8")
    assert '--exclude-module", "pymupdf"' in build
    release=(ROOT/".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    assert "python -m pip install -e '.[api]'" in release
    assert "options: [full, core]" not in release


def test_frozen_pdfium_roundtrip():
    pytest.importorskip("pypdfium2")
    from scripts.frozen_backend_probe import pdf_roundtrip
    assert pdf_roundtrip().startswith(b"\x89PNG\r\n\x1a\n")

def test_docling_two_pass_conversion_accepts_clear_quality_improvement(monkeypatch, tmp_path):
    def module(name):
        created = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, created)
        return created

    module("docling")
    module("docling.datamodel")
    base = module("docling.datamodel.base_models")
    options_mod = module("docling.datamodel.pipeline_options")
    converters = module("docling.document_converter")
    base.InputFormat = types.SimpleNamespace(PDF="pdf")

    class Options:
        def __init__(self):
            self.do_ocr = False
            self.do_table_structure = False
            self.generate_page_images = True
            self.generate_picture_images = True
            self.table_structure_options = types.SimpleNamespace(mode=None)
            self.ocr_options = types.SimpleNamespace(mode="default")
    options_mod.PdfPipelineOptions = Options
    options_mod.TableFormerMode = types.SimpleNamespace(ACCURATE="accurate")
    options_mod.OcrMode = types.SimpleNamespace(FULL_PAGE="full_page")

    attempts = []
    original = [block(1, "中文#" * 50 + "_#_" * 35)]
    recovered = [block(1, "调查结果表明不同地区参与程度有差异。" * 17)]

    class Converter:
        def __init__(self, format_options):
            self.options = format_options["pdf"].pipeline_options
        def convert(self, _path):
            attempts.append((self.options.do_ocr, self.options.ocr_options.mode))
            material = original if self.options.ocr_options.mode == "default" else recovered
            doc = types.SimpleNamespace(
                pages={1: types.SimpleNamespace(size=types.SimpleNamespace(height=1000.0))},
                fake_items=material,
            )
            return types.SimpleNamespace(document=doc, status="success")

    converters.DocumentConverter = Converter
    converters.PdfFormatOption = lambda pipeline_options: types.SimpleNamespace(pipeline_options=pipeline_options)
    monkeypatch.setattr(parser, "_docling_installed", lambda: True)
    monkeypatch.setattr(parser, "_blocks_from_docling_document",
                        lambda doc, *, paper_id: (list(doc.fake_items), []))
    monkeypatch.setattr(parser, "_convert_with_heartbeat",
                        lambda converter, path, *, progress: converter.convert(path))
    monkeypatch.delenv("SRA_DOCLING_FULL_PAGE_OCR", raising=False)
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-file-stub")
    result = parser.DoclingDocumentParser().parse(
        source, paper_id="paper", sha256="sha"
    )
    assert attempts == [(True, "default"), (True, "full_page")]
    assert result.status == ParseStatus.SUCCESS
    assert result.blocks[0].text == recovered[0].text



def test_frozen_bundle_guard_blocks_legacy_pdf_engines(tmp_path):
    from scripts.check_bundle_without_pymupdf import find_legacy_pdf_runtime
    (tmp_path / "_internal").mkdir()
    (tmp_path / "sra-backend.exe").write_bytes(b"synthetic stub")
    assert find_legacy_pdf_runtime(tmp_path) == []
    (tmp_path / "_internal" / "pymupdf").mkdir()
    (tmp_path / "_internal" / "pymupdf" / "_mupdf.pyd").write_bytes(b"stub")
    assert find_legacy_pdf_runtime(tmp_path) == [
        "_internal/pymupdf/_mupdf.pyd"
    ]

def test_lite_json_boundary_accepts_screenshot_case_and_keeps_all_sources():
    import json
    from sociology_research.analyzer import _parse_json_response, _sanitize_model_response
    from sociology_research.models import LiteExtraction
    raw = {
        "metadata_facts": [],
        "basic_facts": [{
            "field_name": "major_finding",
            "statement": "The authors report a significant descriptive difference.",
            "provenance": "AUTHOR_STATED",
            "verification": "SUPPORTED",
            "evidence": refs(4),
        }],
    }
    data = _parse_json_response(json.dumps(raw))
    validated = LiteExtraction.model_validate(
        _sanitize_model_response(data, LiteExtraction)
    )
    assert [x.evidence_id for x in validated.basic_facts[0].evidence] == [
        f"E{i:04d}" for i in range(4)
    ]
