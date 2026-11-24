"""跨文献研究项目（Projects）与确定性证据矩阵（Evidence Matrix）应用服务。

严格遵循 Evidence-first 与零静默计费原则：
- 研究项目删除仅解除项目与论文的多对多关联，绝不删除底层论文与证据卡片。
- `build_evidence_matrix` 仅对本地已保存 `PaperCard` 做确定性结构化对齐并合并
  `ClaimAudit` 审计状态，不触发任何外部模型调用或费用。
"""

from __future__ import annotations

from typing import Any

from ..analysis.reporting import build_deep_reading_report
from ..models import ExtractedClaim, PaperCard, ProjectReadingStatus
from ..repository import ResearchRepository
from .papers import PaperService


class ProjectService:
    """管理研究项目、Reading List 及跨文献证据矩阵聚合。"""

    def __init__(self, repository: ResearchRepository, papers: PaperService) -> None:
        self.repository = repository
        self.papers = papers

    def list_projects(self) -> list[dict[str, Any]]:
        return [proj.model_dump(mode="json") for proj in self.repository.list_projects()]

    def create_project(
        self,
        *,
        name: str,
        description: str = "",
        research_question: str = "",
    ) -> dict[str, Any]:
        project = self.repository.create_project(
            name,
            description=description,
            research_question=research_question,
        )
        return project.model_dump(mode="json")

    def get_project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project id: {project_id}")
        entries = self.repository.list_project_papers(project_id)
        summary_by_id = {item["id"]: item for item in self.papers.list_papers()}
        items: list[dict[str, Any]] = []
        for entry in entries:
            paper_summary = summary_by_id.get(entry.paper_id)
            if paper_summary is None:
                continue
            items.append({
                **entry.model_dump(mode="json"),
                "paper": paper_summary,
            })
        return {
            "project": project.model_dump(mode="json"),
            "papers": items,
        }

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        research_question: str | None = None,
    ) -> dict[str, Any]:
        project = self.repository.update_project(
            project_id,
            name=name,
            description=description,
            research_question=research_question,
        )
        return project.model_dump(mode="json")

    def delete_project(self, project_id: str) -> dict[str, Any]:
        return self.repository.delete_project(project_id)

    def upsert_project_paper(
        self,
        project_id: str,
        paper_id: str,
        *,
        reading_status: ProjectReadingStatus | str = ProjectReadingStatus.QUEUED,
        project_note: str = "",
        position: int | None = None,
    ) -> dict[str, Any]:
        entry = self.repository.add_paper_to_project(
            project_id,
            paper_id,
            reading_status=reading_status,
            project_note=project_note,
            position=position,
        )
        return entry.model_dump(mode="json")

    def remove_project_paper(self, project_id: str, paper_id: str) -> dict[str, Any]:
        removed = self.repository.remove_paper_from_project(project_id, paper_id)
        if not removed:
            raise KeyError(f"paper {paper_id} is not in project {project_id}")
        return {"project_id": project_id, "paper_id": paper_id, "removed": True}

    def build_evidence_matrix(self, project_id: str) -> dict[str, Any]:
        """构建零模型调用、确定性可溯源的跨文献证据对比矩阵。"""
        project = self.repository.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project id: {project_id}")

        entries = self.repository.list_project_papers(project_id)
        rows: list[dict[str, Any]] = []
        analyzed_count = 0

        for entry in entries:
            paper = self.repository.get_paper(entry.paper_id)
            if paper is None:
                continue
            card: PaperCard | None = None
            try:
                card = self.repository.get_card(paper.id)
            except Exception:
                card = None

            if card is not None:
                analyzed_count += 1
            rows.append(self._build_matrix_row(paper, entry, card))

        return {
            "project": project.model_dump(mode="json"),
            "total_papers": len(rows),
            "analyzed_papers": analyzed_count,
            "makes_llm_calls": False,
            "provenance_policy": "STRICT_SOURCE_SEPARATION",
            "rows": rows,
        }

    @staticmethod
    def _serialize_claim_cell(
        claim: ExtractedClaim,
        audit_map: dict[str, Any],
    ) -> dict[str, Any]:
        audit = audit_map.get(claim.claim_id or "") if claim.claim_id else None
        semantic_support = (
            str(audit.semantic_support.value)
            if audit is not None
            else (str(claim.semantic_support.value) if claim.semantic_support is not None else None)
        )
        return {
            "claim_id": claim.claim_id,
            "field_name": claim.field_name,
            "statement": claim.statement,
            "provenance": str(claim.provenance.value),
            "verification": str(claim.verification.value),
            "semantic_support": semantic_support,
            "citations": [
                {
                    "evidence_id": ref.evidence_id,
                    "source_block_id": ref.source_block_id,
                    "page_number": ref.page_number,
                    "quote": ref.quote,
                }
                for ref in claim.evidence
            ],
        }

    @classmethod
    def _build_matrix_row(
        cls,
        paper: Any,
        entry: Any,
        card: PaperCard | None,
    ) -> dict[str, Any]:
        if card is None:
            return {
                "paper_id": paper.id,
                "title": paper.original_name,
                "authors": [],
                "year": None,
                "journal": None,
                "reading_status": str(entry.reading_status.value),
                "project_note": entry.project_note,
                "has_card": False,
                "study_type": None,
                "method": None,
                "identification_strategy": None,
                "population": None,
                "data_source": None,
                "research_questions": [],
                "theories_and_concepts": [],
                "research_design": [],
                "major_findings": [],
                "author_explanations": [],
                "author_limitations": [],
                "ai_limitations": [],
            }

        audit_map = {audit.claim_id: audit for audit in card.claim_audits}
        report = build_deep_reading_report(card)
        sp = card.study_profile

        return {
            "paper_id": paper.id,
            "title": card.metadata.title or paper.original_name,
            "authors": list(card.metadata.authors),
            "year": card.metadata.year,
            "journal": card.metadata.journal,
            "reading_status": str(entry.reading_status.value),
            "project_note": entry.project_note,
            "has_card": True,
            "study_type": str(sp.study_type.value) if sp is not None else None,
            "method": sp.method if sp is not None else None,
            "identification_strategy": sp.identification_strategy if sp is not None else None,
            "population": sp.population if sp is not None else None,
            "data_source": sp.data_source if sp is not None else None,
            "research_questions": [cls._serialize_claim_cell(c, audit_map) for c in report.research_questions],
            "theories_and_concepts": [cls._serialize_claim_cell(c, audit_map) for c in report.theory_and_concepts],
            "research_design": [cls._serialize_claim_cell(c, audit_map) for c in report.study_design],
            "major_findings": [cls._serialize_claim_cell(c, audit_map) for c in report.key_findings],
            "author_explanations": [cls._serialize_claim_cell(c, audit_map) for c in report.author_explanations],
            "author_limitations": [cls._serialize_claim_cell(c, audit_map) for c in report.author_limitations],
            "ai_limitations": [cls._serialize_claim_cell(c, audit_map) for c in report.ai_limitations],
        }
