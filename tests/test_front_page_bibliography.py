"""SRA P0 bibliographic metadata: grounded first-page recovery.

The fixtures intentionally model PDF source blocks, including split Docling
page headers and wrong Lite evidence IDs. A passing test must show real
SourceBlock-backed citations, not inferred or duplicated volume/issue values.
"""
from __future__ import annotations

import pytest

from sociology_research import analyzer as a
from sociology_research.models import (
    LLMMetadataFact, SourceBlock, VerificationStatus,
)


def block(text, id, *, page=1, y=40, role="furniture", label="docling:page_header"):
    return SourceBlock(
        id=id, page_number=page, text=text, role=role,
        source_label=label, bbox=(32.0,float(y),520.0,float(y+25)),
    )


def recovered(blocks):
    registry=a._build_raw_span_registry(blocks)
    candidates=a._derive_layout_metadata_candidates(blocks,registry)
    warnings=[]
    metadata,claims=a._select_metadata(candidates,warnings)
    for claim in claims:
        assert claim.verification == VerificationStatus.SUPPORTED
        assert len(claim.evidence)>0
        for e in claim.evidence:
            assert e.evidence_id in registry
            assert e.source_block_id in {b.id for b in blocks}
            assert e.quote == registry[e.evidence_id].text
            assert registry[e.evidence_id].page_number==e.page_number
    return metadata,claims,warnings


def test_cn_issue_no_volume_or_total_issue_confusion():
    metadata,claims,warnings=recovered([
        block("社会学研究 2021年第1期（总第245期）","front")
    ])
    assert metadata.journal=="社会学研究"
    assert metadata.issue=="1"
    assert metadata.year==2021
    assert metadata.volume is None
    assert metadata.pages is None
    assert "245" not in [str(getattr(metadata,name)) for name in ("issue","volume")]


def test_split_cn_masthead_preserves_accurate_physical_source():
    blocks=[
        block("社会学研究","journal",y=30),
        block("2021年第01期","issue",y=62),
    ]
    metadata,claims,_=recovered(blocks)
    assert metadata.journal=="社会学研究"
    assert metadata.issue=="1"
    assert metadata.year==2021
    journal=next(c for c in claims if c.field_name=="metadata.journal")
    issue=next(c for c in claims if c.field_name=="metadata.issue")
    assert journal.evidence[0].source_block_id=="journal"
    assert issue.evidence[0].source_block_id=="issue"
    assert journal.evidence[0].page_number==1


def test_cn_vol_issue_pages_and_journal_without_journal_suffix():
    metadata,_,_=recovered([
        block("中国人口科学 2022年 第35卷 第3期 pp. 45-59","cn",y=70,role="front_matter",
              label="docling:text")
    ])
    assert (metadata.journal,metadata.year,metadata.volume,metadata.issue,metadata.pages)==(
        "中国人口科学",2022,"35","3","45-59"
    )


def test_english_vol_issue_pages():
    metadata,_,_=recovered([
        block("Journal of Urban Research Vol. 23 No. 2 2021 pp. 30–42","en")
    ])
    assert metadata.journal=="Journal of Urban Research"
    assert metadata.volume=="23"
    assert metadata.issue=="2"
    assert metadata.pages=="30-42"
    assert metadata.year==2021


def test_citation_style_year_volume_issue_page_range():
    metadata,_,_=recovered([
        block("社会科学研究 2021, 45(2): 122-131","citation")
    ])
    assert metadata.journal=="社会科学研究"
    assert metadata.year==2021
    assert metadata.volume=="45"
    assert metadata.issue=="2"
    assert metadata.pages=="122-131"




def test_year_issue_volume_in_three_separate_docling_header_blocks():
    blocks=[
        block("人口与经济","journal",y=25),
        block("第42卷 第6期","numbers",y=53),
        block("2021年","year",y=79),
    ]
    metadata,claims,_=recovered(blocks)
    assert metadata.journal=="人口与经济"
    assert metadata.volume=="42"
    assert metadata.issue=="6"
    assert metadata.year==2021
    by_field={claim.field_name:claim for claim in claims}
    assert by_field["metadata.journal"].evidence[0].source_block_id=="journal"
    assert by_field["metadata.volume"].evidence[0].source_block_id=="numbers"
    assert by_field["metadata.year"].evidence[0].source_block_id=="year"

def test_explicit_dedicated_fields():
    metadata,claims,_=recovered([
        block("刊名：人口与经济","journal",y=70,role="front_matter",label="docling:text"),
        block("卷号：45 期号：03 2021年 页码：101–113","volume",y=96,
              role="front_matter",label="docling:text"),
    ])
    assert metadata.journal=="人口与经济"
    assert metadata.volume=="45"
    assert metadata.issue=="3"
    assert metadata.year==2021
    assert metadata.pages=="101-113"
    assert {c.field_name for c in claims}>={"metadata.journal","metadata.pages"}


def test_bibliographic_pages_not_inferred_from_pdf_page_count():
    metadata,_,_=recovered([
        block("社会学研究 2021年第1期","top"),
        block("11 pages","pagecount",y=200,role="body",label="docling:text"),
    ])
    assert metadata.pages is None


def test_total_issue_is_not_ordinary_issue_or_volume():
    metadata,_,_=recovered([block("某科学期刊 2021年 总第245期","front")])
    assert metadata.issue is None
    assert metadata.volume is None


def test_does_not_read_from_reference_list_page():
    metadata,claims,_=recovered([
        block("《社会学研究》 2021年第3期, pp. 1-20","ref",page=9,role="body",
              label="docling:text")
    ])
    assert metadata.journal is None and metadata.year is None
    assert metadata.issue is None and metadata.pages is None
    assert claims==[]


@pytest.mark.parametrize("content,source_label,role,y",[
    ("No. 1","docling:page_footer","furniture",760),
    ("《社会学研究》2021年第1期","docling:title","front_matter",35),
    ("《社会学研究》2021年第1期","docling:text","body",390),
    ("参见社会学研究2021年第1期对该结论的讨论","docling:text","body",380),
])
def test_no_unrelated_title_footer_or_prose_as_journal(content,source_label,role,y):
    metadata,claims,_=recovered([
        block(content,"unrelated",y=y,role=role,label=source_label)
    ])
    assert not any([
        metadata.journal,metadata.volume,metadata.issue,metadata.pages
    ])
    assert claims==[]


def test_numerical_year_in_body_not_publication_year():
    metadata,_,_=recovered([
        block("研究发现2017年多数人报告变化，2018年略有回升","abstract",
              role="body",label="docling:text",y=260)
    ])
    assert metadata.year is None


def test_biblio_evidence_comes_from_correct_part_of_long_source_block():
    raw=("前置页眉内容 " * 25) + "\nJournal: Sociological Review Vol. 45 No. 2 2020 pp. 21-38"
    assert 180<len(raw)<360,len(raw)
    metadata,claims,_=recovered([
        block(raw,"long-header",role="front_matter",label="docling:text",y=105)
    ])
    assert metadata.journal=="Sociological Review"
    assert metadata.volume=="45"
    assert metadata.issue=="2"
    assert metadata.pages=="21-38"
    for name in ("journal","volume","issue","pages"):
        claim=next(c for c in claims if c.field_name=="metadata."+name)
        assert claim.evidence[0].source_block_id=="long-header"
        assert claim.evidence[0].quote in raw
        assert (
            name != "journal" or "Sociological Review" in claim.evidence[0].quote
        )
        assert claim.evidence[0].evidence_id.startswith("ev_")


def test_lite_wrong_journal_evidence_is_reanchored_to_real_masthead():
    blocks=[
        block("社会学研究 2021年第1期","journal"),
        block("本研究使用2017年样本，对家庭进行了调查。","wrong",role="body",
              label="docling:text",y=320),
    ]
    ledger=a._build_raw_span_registry(blocks)
    wrong_span=next(s for s in ledger.values() if s.source_block_id=="wrong")
    facts=[LLMMetadataFact(
        field_name="journal",value="社会学研究",
        evidence=[{"evidence_id":"E0001"}],
    )]
    warnings=[]
    result=a._resolve_metadata_facts(
        facts,{"E0001":wrong_span},{b.id:b for b in blocks},warnings,
        raw_span_registry=ledger,
    )
    assert len(result)==1
    field,value,claim=result[0]
    assert field=="journal" and value=="社会学研究"
    assert claim.verification==VerificationStatus.SUPPORTED
    assert len(claim.evidence)==1
    assert claim.evidence[0].source_block_id=="journal"
    assert "社会学研究" in claim.evidence[0].quote
    assert any("re-anchored" in message for message in warnings)


def test_lite_volume_digit_in_unrelated_evidence_is_not_accepted():
    blocks=[
        block("第1章 概念测量与解释","body",role="body",label="docling:text",y=300)
    ]
    ledger=a._build_raw_span_registry(blocks)
    wrong_span=next(iter(ledger.values()))
    fact=LLMMetadataFact(
        field_name="volume",value="1",evidence=[{"evidence_id":"E0001"}]
    )
    result=a._resolve_metadata_facts(
        [fact],{"E0001":wrong_span},{"body":blocks[0]},[],
        raw_span_registry=ledger,
    )
    assert result[0][2].verification!=VerificationStatus.SUPPORTED


def test_lite_volume_reanchored_only_if_explicitly_printed():
    blocks=[
        block("第35卷 第3期 2022年","masthead"),
        block("本研究选择第35组访谈资料","wrong",role="body",label="docling:text",y=300)
    ]
    ledger=a._build_raw_span_registry(blocks)
    wrong=next(span for span in ledger.values() if span.source_block_id=="wrong")
    result=a._resolve_metadata_facts(
        [LLMMetadataFact(field_name="volume",value="35",
             evidence=[{"evidence_id":"E0012"}])],
        {"E0012":wrong},{b.id:b for b in blocks},[],
        raw_span_registry=ledger,
    )
    claim=result[0][2]
    assert claim.verification==VerificationStatus.SUPPORTED
    assert claim.evidence[0].source_block_id=="masthead"
    assert "卷" in claim.evidence[0].quote


def test_lite_journal_candidate_only_mentioned_in_body_is_not_promoted():
    blocks=[
        block("理论分析借用了《社会学研究》中的讨论","body",role="body",
              label="docling:text",y=400),
        block("模型变量 5","bad",role="body",label="docling:text",y=500),
    ]
    ledger=a._build_raw_span_registry(blocks)
    wrong=next(span for span in ledger.values() if span.source_block_id=="bad")
    results=a._resolve_metadata_facts(
        [LLMMetadataFact(field_name="journal",value="社会学研究",
             evidence=[{"evidence_id":"E0001"}])],
        {"E0001":wrong},{b.id:b for b in blocks},[],
        raw_span_registry=ledger,
    )
    assert results[0][2].verification!=VerificationStatus.SUPPORTED


def test_existing_printed_page_run_still_works():
    blocks=[
        block(str(41+i),f"p{i}",page=i+1,y=744,role="furniture",
              label="sra:printed_page_number")
        for i in range(4)
    ]
    # Printed paper pages can differ from PDF physical page numbers.
    metadata,claims,_=recovered(blocks)
    assert metadata.pages=="41-44"
    pages=next(c for c in claims if c.field_name=="metadata.pages")
    assert len(pages.evidence)==2
    assert {e.page_number for e in pages.evidence}=={1,4}


def test_span_identity_is_unchanged_by_first_page_scan():
    blocks=[block("社会学研究 2021年第1期","masthead")]
    ledger=a._build_raw_span_registry(blocks)
    baseline={key:span.model_dump() for key,span in ledger.items()}
    a._extract_first_page_bibliography(blocks,ledger)
    assert {key:span.model_dump() for key,span in ledger.items()}==baseline


def test_original_card_warning_does_not_pretend_missing_volume_is_found():
    metadata,claims,warnings=recovered([
        block("《社会学研究》2021年第1期","header")
    ])
    assert metadata.volume is None
    assert all("metadata.volume"!=claim.field_name for claim in claims)


def test_lite_journal_name_cited_in_abstract_is_not_publication_journal():
    # An exact token match in research text does not establish where the
    # article was published, even though older logic accepted it.
    block_body=block("对社会学研究的观点进行批判性讨论","body",
                     role="body",label="docling:text",y=330)
    registry=a._build_raw_span_registry([block_body])
    span=next(iter(registry.values()))
    fact=LLMMetadataFact(
        field_name="journal",value="社会学研究",
        evidence=[{"evidence_id":"E0045"}]
    )
    results=a._resolve_metadata_facts(
        [fact],{"E0045":span},{"body":block_body},[],
        raw_span_registry=registry,
    )
    assert results[0][2].verification==VerificationStatus.NEEDS_REVIEW


def test_lite_study_year_is_not_automatically_publication_year():
    body=block("研究使用了2021年调查结果，变量为家庭收入","body",
               role="body",label="docling:text",y=320)
    registry=a._build_raw_span_registry([body])
    span=next(iter(registry.values()))
    result=a._resolve_metadata_facts(
        [LLMMetadataFact(
            field_name="year",value=2021,evidence=[{"evidence_id":"E0001"}]
        )],
        {"E0001":span},{"body":body},[],
        raw_span_registry=registry,
    )
    assert result[0][2].verification==VerificationStatus.NEEDS_REVIEW


def test_standalone_year_on_header_not_conflated_with_study_year():
    metadata,claims,_=recovered([
        block("社会学研究","journal",y=20),
        block("2021年","year",y=45)
    ])
    assert metadata.year==2021
    assert metadata.journal is None  # No issue or labeled journal; avoid guessing.
    year_claim=next(c for c in claims if c.field_name=="metadata.year")
    assert year_claim.evidence[0].source_block_id=="year"



def test_pdf_to_saved_papercard_overview_metadata_without_model_guess(monkeypatch):
    from types import SimpleNamespace
    from sociology_research.models import LiteExtraction, ProAnalysis
    journal=block("社会学研究 2021年第1期","header",y=25)
    abstract=block(
        "本文采用社会调查数据，对二孩生育意愿与家庭观念之间的关系进行描述。"
        "所用数据包含城乡样本，重点解释不同家庭结构之间的差异。",
        "paragraph",role="body",label="docling:text",y=350,
    )
    blocks=[journal,abstract]
    record=SimpleNamespace(
        id="paper-1",page_count=1,warnings=[],
        parser_version="test-parser",stored_path="/unused.pdf",sha256="test-sha"
    )

    class Repo:
        saved=None
        def get_paper(self,paper_id): return record
        def get_source_blocks(self,paper_id): return blocks
        def get_figures(self,paper_id): return []
        def save_card(self,card): self.saved=card

    fake_settings=SimpleNamespace(
        require_lite_configuration=lambda:(
            "never-used","https://example.invalid/v1","Mock-Lite"
        ),
        require_pro_configuration=lambda:(
            "never-used","https://example.invalid/v1","Mock-Pro"
        ),
    )
    monkeypatch.setattr(
        a,"create_default_parser",lambda:SimpleNamespace(version="test-parser")
    )
    repo=Repo()
    pipeline=a.PaperAnalysisPipeline(
        repo,settings=fake_settings,client=object()
    )
    calls=[]
    def fake_cached_call(*args,**kwargs):
        calls.append(args[1])
        return LiteExtraction() if args[1]=="lite" else ProAnalysis()
    monkeypatch.setattr(pipeline,"_cached_call",fake_cached_call)

    card=pipeline.analyze("paper-1")
    assert repo.saved is card
    assert card.metadata.journal=="社会学研究"
    assert card.metadata.year==2021
    assert card.metadata.issue=="1"
    assert card.metadata.volume is None
    assert card.metadata.pages is None
    assert {
        x.field_name for x in card.metadata_claims
    }>={"metadata.journal","metadata.issue","metadata.year"}
    stored_span_ids={span.id for span in card.evidence_spans}
    assert all(
        evidence.evidence_id in stored_span_ids
        for claim in card.metadata_claims
        for evidence in claim.evidence
    )
    assert "lite" in calls
    # The model fake returned no metadata: these are parser-grounded values.
