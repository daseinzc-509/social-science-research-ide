from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from sociology_research import analyzer, parser
from sociology_research.analyzer import PaperAnalysisPipeline
from sociology_research.models import (
    EvidenceReference,
    ExtractedClaim,
    PaperCard,
    Provenance,
    VerificationStatus,
)
from sociology_research.pipeline import ImportPipeline
from sociology_research.repository import ResearchRepository

GOOD_CN = (
    "社会学者长期以来聚焦于因果识别的方法及其应用，却对因果发现的相关进展缺乏关注。"
    "在回顾因果推断两种理论传统的基础上，从算法原理、应用路径和方法关联三个方面，"
    "系统介绍因果发现的经典算法，探讨它们在研究实践中的应用方式，以及与其他计算社会学方法结合的可能性。"
)
GOOD_MIXED = (
    "本文比较 AI、LLM 与 DAG 方法，并使用 R、Python 和 SEM 模型分析数据。"
    "变量 x、y 与 z 分别代表处理、结果与控制变量。研究结果显示模型拟合良好。"
)
BAD_CN = (
    "社会学者长期 u 乘聚焦于因果代别的方法反其应用，舞#?乘线现的惠 I 海输罢关注。"
    "施_顾因套口鲍:均_上，从#法原气应用路方法关联三个方面^专统介绍辅肇发现?镣襄集%"
    "探口讨囊们在研究实践中?佳用#逢，邀减其他计参蘸会合雷可_fe。面对大语#lali_:fc，_^;;"
    "爱口镣锤■法的is f l将陪予计#_学以新的擎产镯，扬*数据参豹的?知_生气f计算社会学更馨入理庠_#释口复杂社会现象"
)


def make_pdf(path: Path, text: str | None, pages: int = 4) -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_textbox(
                fitz.Rect(60, 60, 540, 760),
                (text + "\n") * 4,
                fontname="china-s",
                fontsize=10,
            )
    doc.save(path)
    doc.close()


def metadata_claim(field: str, quote: str) -> ExtractedClaim:
    return ExtractedClaim(
        field_name=f"metadata.{field}",
        statement="test",
        provenance=Provenance.AUTHOR_STATED,
        verification=VerificationStatus.SUPPORTED,
        evidence=[
            EvidenceReference(
                evidence_id="evidence01",
                source_block_id="block01",
                page_number=1,
                quote=quote,
            )
        ],
    )


def main() -> None:
    assert parser._garbled_text_score(GOOD_CN) == 0
    assert not parser._looks_like_garbled_text(GOOD_CN)
    assert not parser._looks_like_garbled_text(GOOD_MIXED)
    assert parser._looks_like_garbled_text(BAD_CN)
    assert parser._looks_like_garbled_text(("正常中文�异常字符�" * 10))
    assert not parser._looks_like_garbled_text("短文本#_u")

    with TemporaryDirectory() as td:
        root = Path(td)
        good_pdf = root / "good.pdf"
        bad_pdf = root / "bad.pdf"
        blank_pdf = root / "blank.pdf"
        make_pdf(good_pdf, GOOD_CN)
        make_pdf(bad_pdf, BAD_CN)
        make_pdf(blank_pdf, None, pages=3)

        good_profile = parser._pdf_text_layer_profile(good_pdf)
        bad_profile = parser._pdf_text_layer_profile(bad_pdf)
        blank_profile = parser._pdf_text_layer_profile(blank_pdf)
        assert not good_profile.needs_ocr and not good_profile.force_full_page_ocr
        assert bad_profile.needs_ocr and bad_profile.force_full_page_ocr
        assert bad_profile.suspicious_pages == 4
        assert blank_profile.needs_ocr and not blank_profile.force_full_page_ocr

        good_result = parser.PyMuPDFDocumentParser().parse(
            good_pdf, paper_id="good", sha256="0" * 64
        )
        bad_result = parser.PyMuPDFDocumentParser().parse(
            bad_pdf, paper_id="bad", sha256="1" * 64
        )
        blank_result = parser.PyMuPDFDocumentParser().parse(
            blank_pdf, paper_id="blank", sha256="2" * 64
        )
        assert good_result.status.value == "success"
        assert bad_result.status.value == "needs_review"
        assert any("corrupted" in warning for warning in bad_result.warnings)
        assert blank_result.status.value == "needs_review"

        class OcrOptions:
            force_full_page_ocr = False

        class Options:
            ocr_options = OcrOptions()

        options = Options()
        assert parser._enable_full_page_ocr(options)
        assert options.ocr_options.force_full_page_ocr is True
        assert not parser._enable_full_page_ocr(object())

        # Parser-version refresh must invalidate stale cards/cache before analysis.
        class OldParser(parser.PyMuPDFDocumentParser):
            version = "old-parser-version"

        repo_root = root / "repo"
        repo = ResearchRepository(repo_root / "research.sqlite3")
        record, _, _ = ImportPipeline(repo_root, repo, parser=OldParser()).import_pdf(good_pdf)
        repo.save_card(
            PaperCard(
                paper_id=record.id,
                lite_model="lite",
                pro_model="pro",
                prompt_version="old",
            )
        )
        repo.save_model_run(record.id, "lite", "lite", "old", "digest", "{}")
        pipeline = PaperAnalysisPipeline(repo)
        pipeline.parser = parser.PyMuPDFDocumentParser()
        refreshed, blocks = pipeline._load_paper_blocks(record.id, exclude_after_text=None)
        assert refreshed.parser_version == parser.PyMuPDFDocumentParser.version
        assert blocks
        assert repo.get_card(record.id) is None
        assert repo.get_model_run(record.id, "lite", "lite", "old", "digest") is None

    assert analyzer._coerce_metadata_value(
        "doi", "https://doi.org/10.19862/j.cnki.xsyk.001104"
    ) == "10.19862/j.cnki.xsyk.001104"
    assert analyzer._metadata_compare_key("issue", "06") == analyzer._metadata_compare_key("issue", "6")
    assert analyzer._metadata_compare_key(
        "title", "发现因果的算法：被忽视的计算社会学工具箱"
    ) == analyzer._metadata_compare_key(
        "title", "发现因果的算法: 被忽视的计算社会学工具箱"
    )
    assert analyzer._metadata_compare_key(
        "keywords", ["causal inference", "data-driven"]
    ) == analyzer._metadata_compare_key(
        "keywords", ["data driven", "causal inference"]
    )

    warnings: list[str] = []
    metadata, selected = analyzer._select_metadata(
        [
            (
                "title",
                "发现因果的算法：被忽视的计算社会学工具箱",
                metadata_claim("title", "发现因果的算法：被忽视的计算社会学工具箱"),
            ),
            (
                "title",
                "发现因果的算法: 被忽视的计算社会学工具箱",
                metadata_claim("title", "发现因果的算法: 被忽视的计算社会学工具箱"),
            ),
            ("issue", "06", metadata_claim("issue", "学术月刊 2025.6")),
            ("issue", "6", metadata_claim("issue", "学术月刊 2025.6")),
        ],
        warnings,
    )
    assert not warnings
    assert metadata.issue == "06"
    assert len(selected) == 2

    warnings = []
    analyzer._select_metadata(
        [
            ("title", "正确标题", metadata_claim("title", "正确标题")),
            ("title", "另一个标题", metadata_claim("title", "另一个标题")),
        ],
        warnings,
    )
    assert len(warnings) == 1 and "conflicting supported metadata" in warnings[0]

    assert analyzer._metadata_value_supported_by_evidence(
        "issue", "06", metadata_claim("issue", "学术月刊 2025.6")
    )
    assert not analyzer._metadata_value_supported_by_evidence(
        "issue", "06", metadata_claim("issue", "第112页，共6张图")
    )
    assert analyzer._metadata_value_supported_by_evidence(
        "doi",
        "10.19862/j.cnki.xsyk.001104",
        metadata_claim("doi", "DOI: 10.19862/j.cnki.xsyk.001104"),
    )

    print("all bugfix checks passed")


if __name__ == "__main__":
    main()
