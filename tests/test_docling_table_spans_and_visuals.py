"""Regression tests for merged tables, optional formulas and figure provenance.

All tests are offline. They do NOT download Docling weights or call any AI API.
The actual Windows/Avalonia native build remains a separate release gate.
"""
from __future__ import annotations

import sys
import sqlite3
import types
from pathlib import Path

import pytest

from sociology_research import parser
from sociology_research.models import (
    FigureRegion, PaperRecord, ParsedDocument, ParseStatus, SourceBlock,
    TableCellSpan, PaperCard,
)
from sociology_research.repository import ResearchRepository


def cell(text, row, col, row_span=1, col_span=1, header=False):
    return types.SimpleNamespace(
        text=text,
        start_row_offset_idx=row,
        end_row_offset_idx=row+row_span,
        start_col_offset_idx=col,
        end_col_offset_idx=col+col_span,
        column_header=header,
        row_header=False,
    )


def table(rows, cells):
    return types.SimpleNamespace(
        data=types.SimpleNamespace(
            grid=[
                [types.SimpleNamespace(text=value) for value in row]
                for row in rows
            ],
            table_cells=cells,
        )
    )


def test_docling_preserves_merged_headers_without_guessing_from_repetition():
    rows = [
        ["研究类型", "理想子女性别结构", "理想子女性别结构", "样本量"],
        ["", "0男9女", "1男1女", ""],
        ["总体", "5.1", "78.5", "31,629"],
    ]
    source = table(rows, [
        cell("研究类型", 0, 0, 2, 1, True),
        cell("理想子女性别结构", 0, 1, 1, 2, True),
        cell("样本量", 0, 3, 2, 1, True),
        cell("0男9女", 1, 1, header=True),
        cell("1男1女", 1, 2, header=True),
        cell("总体", 2, 0),
        cell("5.1", 2, 1),
        cell("78.5", 2, 2),
        cell("31,629", 2, 3),
    ])
    flat = parser._docling_table_rows(source, object())
    result, warning = parser._docling_table_cells(source, flat)
    assert warning is None
    assert flat == rows
    assert len(result) == 9
    parent = result[1]
    assert (parent.row, parent.col, parent.row_span, parent.col_span) == (0, 1, 1, 2)
    assert parent.column_header
    assert parent.text == "理想子女性别结构"
    # Exactly the observed source position; no synthetic header inferred.


def test_invalid_span_collision_is_rejected_without_changing_raw_rows():
    rows = [["first", "second"], ["data", "42"]]
    source = table(rows, [
        cell("first", 0, 0, 1, 2),
        cell("overlap", 0, 1),
    ])
    original = parser._docling_table_rows(source, object())
    spans, warning = parser._docling_table_cells(source, original)
    assert spans is None
    assert "overlapping" in warning
    assert original == rows


def test_blank_structural_grid_row_does_not_shift_span_offsets():
    rows = [["header", "other"], ["", ""], ["datum", "42"]]
    source = table(rows, [cell("header", 0, 0), cell("datum", 2, 0)])
    got = parser._docling_table_rows(source, object())
    assert got == rows
    spans, warning = parser._docling_table_cells(source, got)
    assert warning is None
    assert spans[-1].row == 2


def test_table_data_without_first_class_spans_remains_compatible():
    source = table([["a", "b"], ["c", "d"]], [])
    got = parser._docling_table_rows(source, object())
    spans, warning = parser._docling_table_cells(source, got)
    assert spans is None and warning is None


def test_located_pictures_are_preserved_but_unlocated_ones_not_invented():
    bbox = types.SimpleNamespace(l=10, t=20, r=130, b=210)
    prov = types.SimpleNamespace(page_no=5, bbox=bbox)
    located = types.SimpleNamespace(
        label="picture", captions=["#/texts/0"],
        prov=[prov], text="",
    )
    unlocated = types.SimpleNamespace(label="picture", prov=[], text="")
    doc = types.SimpleNamespace(
        iterate_items=lambda: iter([(located, 1), (unlocated, 1)]),
        resolve_ref=lambda ref: types.SimpleNamespace(text="图 1 理论模型"),
        pages={5: types.SimpleNamespace(size=types.SimpleNamespace(height=1000))},
    )
    figures = parser._docling_figure_regions(doc, paper_id="paper-1")
    assert len(figures) == 1
    assert figures[0].page_number == 5
    assert figures[0].caption == "图 1 理论模型"
    assert figures[0].bbox == (10., 20., 130., 210.)


def make_document(text="2,500", *, version="test-parser-v2"):
    spans = [
        TableCellSpan(row=0, col=0, col_span=2, text="N", column_header=True),
        TableCellSpan(row=1, col=0, text="1,000"),
        TableCellSpan(row=1, col=1, text=text),
    ]
    block = SourceBlock(
        id="table-1", page_number=1,
        text=f"N\\n1,000\\t{text}", bbox=(10,20,150,200),
        table_rows=[["N","N"],["1,000",text]],
        table_cells=spans, role="table", source_label="docling:table",
    )
    figure = FigureRegion(
        id="figure-1", page_number=1,
        bbox=(200, 40, 400, 300), caption="图一：研究框架",
    )
    return ParsedDocument(
        paper_id="paper-1", sha256="hash-1", source_path="/tmp/paper-1.pdf",
        page_count=1, parser_version=version,
        status=ParseStatus.SUCCESS, blocks=[block], figures=[figure],
    )


def test_new_database_roundtrip_table_spans_and_figures(tmp_path):
    repository = ResearchRepository(tmp_path/"sra.sqlite")
    doc = make_document()
    paper = PaperRecord(
        id="paper-1",sha256="hash-1",original_name="paper.pdf",
        stored_path="/tmp/paper-1.pdf",page_count=1,
        parser_version=doc.parser_version,parse_status=ParseStatus.SUCCESS,
        created_at="2026-10-11T00:00:00Z",
    )
    repository.save_import(paper, doc)
    loaded=repository.get_source_blocks("paper-1")
    assert len(loaded)==1 and loaded[0].table_cells is not None
    assert loaded[0].table_cells[0].col_span==2
    assert loaded[0].table_cells[2].text=="2,500"
    figures=repository.get_figures("paper-1")
    assert len(figures)==1 and figures[0].bbox==(200.,40.,400.,300.)

    replacement=make_document("3,200",version="test-parser-v3")
    repository.refresh_parsed_document("paper-1",replacement)
    assert repository.get_source_blocks("paper-1")[0].table_cells[2].text=="3,200"
    assert len(repository.get_figures("paper-1"))==1


def test_old_sqlite_layout_gets_additive_migration(tmp_path):
    path=tmp_path/"old.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE source_blocks (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, page_number INTEGER NOT NULL,
            text TEXT NOT NULL, bbox_json TEXT, table_rows_json TEXT,
            role TEXT, source_label TEXT
        )""")
    repository=ResearchRepository(path)
    with sqlite3.connect(path) as con:
        columns={row[1] for row in con.execute("PRAGMA table_info(source_blocks)")}
        figures_exists=con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='figure_regions'"
        ).fetchone()[0]
    assert "table_cells_json" in columns
    assert figures_exists==1
    assert repository.get_figures("nobody")==[]


def test_optional_formula_enrichment_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SRA_DOCLING_FORMULA_ENRICHMENT", raising=False)
    monkeypatch.delenv("SRA_FORMULA_ENRICHMENT", raising=False)
    # Keep this test independent of a developer's persisted UI preference.
    from sociology_research import user_storage
    monkeypatch.setattr(user_storage, "read_user_preferences", lambda: {})
    plain = parser.DoclingDocumentParser()
    assert not plain.formula_enabled
    assert "default" in plain.version
    monkeypatch.setenv("SRA_DOCLING_FORMULA_ENRICHMENT","1")
    enabled=parser.DoclingDocumentParser()
    assert enabled.formula_enabled
    assert "formula" in enabled.version
    assert enabled.version != plain.version


def test_formula_mode_fallback_to_standard_does_not_discard_document(monkeypatch, tmp_path):
    for name in [
        "docling", "docling.datamodel", "docling.datamodel.base_models",
        "docling.datamodel.pipeline_options", "docling.document_converter",
    ]:
        monkeypatch.setitem(sys.modules,name,types.ModuleType(name))
    sys.modules["docling.datamodel.base_models"].InputFormat = types.SimpleNamespace(PDF="pdf")
    pipeline = sys.modules["docling.datamodel.pipeline_options"]
    class Options:
        def __init__(self):
            self.do_formula_enrichment=False
            self.do_ocr=False
            self.do_table_structure=False
            self.generate_page_images=True
            self.generate_picture_images=True
            self.table_structure_options=types.SimpleNamespace(mode=None)
    pipeline.PdfPipelineOptions=Options
    pipeline.TableFormerMode=types.SimpleNamespace(ACCURATE="accurate")
    converters=sys.modules["docling.document_converter"]
    attempts=[]
    fake_document=types.SimpleNamespace(
        pages={1:types.SimpleNamespace(size=types.SimpleNamespace(height=700))},
        iterate_items=lambda **kwargs: iter([]),
    )
    class Converter:
        def __init__(self,format_options):
            self.options=format_options["pdf"].pipeline_options
        def convert(self,path):
            attempts.append(self.options.do_formula_enrichment)
            if self.options.do_formula_enrichment:
                raise RuntimeError("optional formula weights missing")
            return types.SimpleNamespace(document=fake_document,status="success")
    converters.DocumentConverter=Converter
    converters.PdfFormatOption=lambda pipeline_options:types.SimpleNamespace(
        pipeline_options=pipeline_options
    )
    monkeypatch.setattr(parser, "_docling_installed",lambda:True)
    # Simulate a previously verified one-page text extraction; no real PDF I/O.
    text_block=SourceBlock(id="body",page_number=1,text="A readable document paragraph.")
    monkeypatch.setattr(parser, "_blocks_from_docling_document",
                        lambda doc, *,paper_id:([text_block],[]))
    monkeypatch.setattr(parser, "_convert_with_heartbeat",
                        lambda converter,path, *,progress:converter.convert(path))
    monkeypatch.setenv("SRA_DOCLING_FORMULA_ENRICHMENT","1")
    path=tmp_path/"sample.pdf"
    path.write_bytes(b"%PDF-fake")
    parsed=parser.DoclingDocumentParser().parse(path,paper_id="paper",sha256="sha")
    assert attempts==[True,False]
    assert parsed.status==ParseStatus.NEEDS_REVIEW
    assert parsed.blocks[0].text==text_block.text
    assert any("formula enrichment failed" in warning.casefold() for warning in parsed.warnings)


def test_paper_card_schema_keeps_old_documents_compatible():
    assert "figures" in PaperCard.model_fields
    assert "formulas" in PaperCard.model_fields
    assert PaperCard.model_fields["figures"].default_factory() == []
    assert PaperCard.model_fields["formulas"].default_factory() == []


def test_paper_card_json_preserves_span_and_figure_schema():
    document=make_document()
    card=PaperCard(
        paper_id="paper-1",lite_model="lite",pro_model="pro",
        prompt_version="test-v1",tables=document.blocks,
        figures=document.figures,
    )
    dumped=card.model_dump(mode="json")
    assert dumped["tables"][0]["table_cells"][0]["col_span"] == 2
    assert dumped["figures"][0]["page_number"] == 1
    assert dumped["figures"][0]["caption"] == "图一：研究框架"
    assert dumped["formulas"] == []


def test_parser_extracts_spans_from_faked_docling_document():
    values=[["研究类别","二孩生育意愿","二孩生育意愿"],["","男","女"],["合计","80.1","74.8"]]
    table_obj=table(values,[
        cell("研究类别",0,0,2,1,True),
        cell("二孩生育意愿",0,1,1,2,True),
        cell("男",1,1,header=True),
        cell("女",1,2,header=True),
        cell("合计",2,0),
        cell("80.1",2,1),
        cell("74.8",2,2),
    ])
    table_obj.label="table"
    table_obj.content_layer="body"
    table_obj.prov=[
        types.SimpleNamespace(
            page_no=4,
            bbox=types.SimpleNamespace(l=15,t=30,r=200,b=250),
        )
    ]
    document=types.SimpleNamespace(
        iterate_items=lambda **kwargs: iter([(table_obj,1)]),
        pages={4:types.SimpleNamespace(size=types.SimpleNamespace(height=720))},
    )
    blocks,warnings=parser._blocks_from_docling_document(
        document,paper_id="test-table"
    )
    assert warnings==[]
    assert len(blocks)==1
    assert blocks[0].table_rows==values
    assert blocks[0].table_cells[1].col_span==2
    assert blocks[0].bbox==(15.,30.,200.,250.)
