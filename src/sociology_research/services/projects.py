"""跨文献研究项目（Projects）与确定性证据矩阵（Evidence Matrix）应用服务。

严格遵循 Evidence-first 与零静默计费原则：
- 研究项目删除仅解除项目与论文的多对多关联，绝不删除底层论文与证据卡片。
- `build_evidence_matrix` 仅对本地已保存 `PaperCard` 做确定性结构化对齐并合并
  `ClaimAudit` 审计状态，不触发任何外部模型调用或费用。
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from ..analysis.reporting import build_deep_reading_report
from ..models import ExtractedClaim, PaperCard, ProjectReadingStatus, compute_claim_id
from ..repository import ResearchRepository
from .papers import PaperService

_READING_STATUS_ZH: dict[str, str] = {
    "queued": "待读",
    "reading": "在读",
    "reviewed": "已复核",
    "core": "核心文献",
}

_STUDY_TYPE_ZH: dict[str, str] = {
    "quantitative_observational": "定量观察研究",
    "experimental": "实验研究",
    "qualitative": "质性研究",
    "mixed_methods": "混合方法",
    "theoretical": "理论探讨",
    "review": "文献综述",
    "methodological": "方法论研究",
}

_HUMAN_REVIEW_ZH: dict[str, str] = {
    "confirmed": "已确认",
    "corrected": "人工修正",
    "rejected": "存疑驳回",
    "unresolved": "待定",
}


def _safe_csv_cell(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if text and text[0] in ("=", "@"):
        return "'" + text
    return text


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
        notes = self.repository.list_project_notes(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "papers": items,
            "notes": [n.model_dump(mode="json") for n in notes],
        }

    def list_project_notes(self, project_id: str) -> list[dict[str, Any]]:
        return [n.model_dump(mode="json") for n in self.repository.list_project_notes(project_id)]

    def create_project_note(
        self,
        project_id: str,
        *,
        note: str,
        title: str = "",
    ) -> dict[str, Any]:
        note_id = f"pnote-{uuid.uuid4().hex[:12]}"
        created = self.repository.add_project_note(
            project_id,
            note_id,
            note,
            title=title,
        )
        return created.model_dump(mode="json")

    def update_project_note(
        self,
        project_id: str,
        note_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        updated = self.repository.update_project_note(
            project_id,
            note_id,
            title=title,
            note=note,
        )
        return updated.model_dump(mode="json")

    def delete_project_note(self, project_id: str, note_id: str) -> dict[str, Any]:
        deleted = self.repository.delete_project_note(project_id, note_id)
        if not deleted:
            raise KeyError(f"unknown project note id: {note_id}")
        return {"project_id": project_id, "note_id": note_id, "deleted": True}

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
            reviews_by_claim = {
                rev.claim_id: rev for rev in self.repository.list_claim_reviews(paper.id)
            }
            rows.append(self._build_matrix_row(paper, entry, card, reviews_by_claim))

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
        review_map: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = claim.claim_id or compute_claim_id(claim.field_name, claim.statement)
        audit = audit_map.get(cid)
        semantic_support = (
            str(audit.semantic_support.value)
            if audit is not None
            else (str(claim.semantic_support.value) if claim.semantic_support is not None else None)
        )
        human_rev = review_map.get(cid) if review_map else None
        human_review_payload = (
            {
                "user_status": str(human_rev.user_status.value),
                "corrected_statement": human_rev.corrected_statement,
                "note": human_rev.note,
                "updated_at": human_rev.updated_at,
            }
            if human_rev is not None
            else None
        )
        return {
            "claim_id": cid,
            "field_name": claim.field_name,
            "statement": claim.statement,
            "provenance": str(claim.provenance.value),
            "verification": str(claim.verification.value),
            "semantic_support": semantic_support,
            "human_review": human_review_payload,
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
        review_map: dict[str, Any] | None = None,
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
            "research_questions": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.research_questions],
            "theories_and_concepts": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.theory_and_concepts],
            "research_design": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.study_design],
            "major_findings": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.key_findings],
            "author_explanations": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.author_explanations],
            "author_limitations": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.author_limitations],
            "ai_limitations": [cls._serialize_claim_cell(c, audit_map, review_map) for c in report.ai_limitations],
        }

    @staticmethod
    def _format_claims_for_csv(cells: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for idx, cell in enumerate(cells, start=1):
            stmt = str(cell.get("statement") or "").strip()
            if not stmt:
                continue
            pages = sorted({
                int(c["page_number"])
                for c in (cell.get("citations") or [])
                if isinstance(c, dict) and c.get("page_number")
            })
            page_suffix = f" (p.{', p.'.join(str(p) for p in pages)})" if pages else ""
            hr = cell.get("human_review")
            hr_tag = ""
            extra_parts: list[str] = []
            if isinstance(hr, dict) and hr.get("user_status"):
                st = str(hr["user_status"])
                hr_tag = f"[人工审定:{_HUMAN_REVIEW_ZH.get(st, st)}] "
                corr = str(hr.get("corrected_statement") or "").strip()
                if corr:
                    extra_parts.append(f"人工修正：{corr}")
                note = str(hr.get("note") or "").strip()
                if note:
                    extra_parts.append(f"备注：{note}")
            entry = f"[{idx}] {hr_tag}{stmt}{page_suffix}"
            if extra_parts:
                entry += " | " + "；".join(extra_parts)
            lines.append(entry)
        return "\n".join(lines)

    def render_evidence_matrix_csv(self, project_id: str) -> str:
        """导出零模型调用、Excel 友好（UTF-8 BOM）的专题证据对比矩阵 CSV。"""
        matrix = self.build_evidence_matrix(project_id)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([
            "论文标题",
            "作者",
            "年份",
            "期刊",
            "阅读状态",
            "专题备注",
            "是否已分析",
            "研究类型",
            "研究方法",
            "识别/推断策略",
            "研究对象",
            "数据源",
            "研究问题 (AUTHOR)",
            "核心发现与人工审定 (AUTHOR / HUMAN_REVIEWED)",
            "作者解释与机制 (AUTHOR)",
            "作者自述局限 (AUTHOR)",
            "AI 方法学局限与审定 (AI REVIEW / HUMAN_REVIEWED)",
            "关键引文页码",
        ])
        for row in matrix["rows"]:
            all_pages: set[int] = set()
            for key in (
                "research_questions",
                "major_findings",
                "author_explanations",
                "author_limitations",
                "ai_limitations",
            ):
                for cell in row.get(key) or []:
                    for cite in cell.get("citations") or []:
                        if cite.get("page_number"):
                            all_pages.add(int(cite["page_number"]))
            pages_str = ", ".join(f"p.{p}" for p in sorted(all_pages))
            reading_label = _READING_STATUS_ZH.get(str(row.get("reading_status") or ""), str(row.get("reading_status") or ""))
            study_label = _STUDY_TYPE_ZH.get(str(row.get("study_type") or ""), str(row.get("study_type") or ""))
            writer.writerow([
                _safe_csv_cell(row.get("title")),
                _safe_csv_cell("、".join(row.get("authors") or [])),
                _safe_csv_cell(row.get("year") if row.get("year") is not None else ""),
                _safe_csv_cell(row.get("journal") or ""),
                _safe_csv_cell(reading_label),
                _safe_csv_cell(row.get("project_note") or ""),
                "已分析" if row.get("has_card") else "未分析",
                _safe_csv_cell(study_label),
                _safe_csv_cell(row.get("method") or ""),
                _safe_csv_cell(row.get("identification_strategy") or ""),
                _safe_csv_cell(row.get("population") or ""),
                _safe_csv_cell(row.get("data_source") or ""),
                _safe_csv_cell(self._format_claims_for_csv(row.get("research_questions") or [])),
                _safe_csv_cell(self._format_claims_for_csv(row.get("major_findings") or [])),
                _safe_csv_cell(self._format_claims_for_csv(row.get("author_explanations") or [])),
                _safe_csv_cell(self._format_claims_for_csv(row.get("author_limitations") or [])),
                _safe_csv_cell(self._format_claims_for_csv(row.get("ai_limitations") or [])),
                _safe_csv_cell(pages_str),
            ])
        return "\ufeff" + buffer.getvalue()

    def render_evidence_matrix_markdown(self, project_id: str) -> str:
        """导出含专题研究笔记（MY NOTE）与人工审定状态的确定性 Evidence Matrix Markdown。"""
        matrix = self.build_evidence_matrix(project_id)
        project = matrix["project"]
        project_notes = self.repository.list_project_notes(project_id)

        def esc(val: object) -> str:
            text = "—" if val in (None, "") else str(val)
            return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")

        lines = [f"# Evidence Matrix · {project['name']}", ""]
        if project.get("research_question"):
            lines += [f"> **核心研究问题**：{project['research_question']}", ""]
        if project.get("description"):
            lines += [f"> **专题说明**：{project['description']}", ""]
        lines += [
            f"- 统计：共 {matrix['total_papers']} 篇文献，已分析 {matrix['analyzed_papers']} 篇（纯本地确定性聚合，零 LLM 调用）",
            "",
        ]

        if project_notes:
            lines += ["## 1. 专题研究笔记（MY NOTE）", ""]
            for pnote in project_notes:
                heading = pnote.title.strip() if pnote.title else "专题笔记"
                ts = (pnote.updated_at or pnote.created_at)[:16].replace("T", " ")
                lines.append(f"### {heading}")
                if ts:
                    lines.append(f"_更新于：{ts} · 来源：MY NOTE_")
                    lines.append("")
                lines.append(pnote.note.strip())
                lines.append("")

        sec_offset = 1 if project_notes else 0
        lines += [
            f"## {1 + sec_offset}. 研究设计总览表",
            "",
            "| 论文 | 阅读状态 | 研究类型 | 方法 | 识别 / 推断策略 | 群体与数据 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in matrix["rows"]:
            authors = "、".join(row.get("authors") or [])
            year = str(row["year"]) if row.get("year") is not None else ""
            meta = " · ".join(p for p in (authors, year, row.get("journal") or "") if p) or "书目待补"
            pop_data = " · ".join(p for p in (row.get("population") or "", row.get("data_source") or "") if p) or "—"
            r_status = _READING_STATUS_ZH.get(str(row.get("reading_status") or ""), str(row.get("reading_status") or ""))
            s_type = _STUDY_TYPE_ZH.get(str(row.get("study_type") or ""), str(row.get("study_type") or "待分析"))
            lines.append(
                f"| {esc(row['title'])} ({esc(meta)}) | {esc(r_status)} | {esc(s_type)} | {esc(row.get('method'))} | {esc(row.get('identification_strategy'))} | {esc(pop_data)} |"
            )

        lines += ["", f"## {2 + sec_offset}. 逐篇主张与证据对照（严格区分 AUTHOR / AI REVIEW / HUMAN_REVIEWED）", ""]
        for row in matrix["rows"]:
            authors = "、".join(row.get("authors") or [])
            year = str(row["year"]) if row.get("year") is not None else ""
            meta = " · ".join(p for p in (authors, year, row.get("journal") or "") if p) or "书目待补"
            r_status = _READING_STATUS_ZH.get(str(row.get("reading_status") or ""), str(row.get("reading_status") or ""))
            lines.append(f"### {row['title']}")
            lines.append(f"- **书目**：{meta} · **阅读状态**：{r_status}")
            if row.get("project_note"):
                lines.append(f"- **专题备注**：{row['project_note']}")
            if not row.get("has_card"):
                lines += ["- *尚未完成 Paper Card 分析。*", ""]
                continue

            for group_title, key in (
                ("研究问题", "research_questions"),
                ("核心发现", "major_findings"),
                ("作者解释 / 机制", "author_explanations"),
                ("作者自述局限", "author_limitations"),
                ("AI 方法学审读局限", "ai_limitations"),
            ):
                cells = row.get(key) or []
                if not cells:
                    continue
                lines.append(f"- **{group_title}**")
                for cell in cells:
                    prov = "AUTHOR" if cell.get("provenance") == "AUTHOR_STATED" else (
                        "AI REVIEW" if cell.get("provenance") == "AI_INFERRED" else str(cell.get("provenance") or "")
                    )
                    hr = cell.get("human_review")
                    hr_badge = ""
                    if isinstance(hr, dict) and hr.get("user_status"):
                        st = str(hr["user_status"])
                        hr_badge = f" [人工审定：{_HUMAN_REVIEW_ZH.get(st, st)}]"
                    pages = sorted({
                        int(c["page_number"])
                        for c in (cell.get("citations") or [])
                        if isinstance(c, dict) and c.get("page_number")
                    })
                    page_suffix = f" ({', '.join(f'p.{p}' for p in pages)})" if pages else ""
                    lines.append(f"  - `[{prov}]`{hr_badge} {cell['statement']}{page_suffix}")
                    if isinstance(hr, dict):
                        if hr.get("corrected_statement"):
                            lines.append(f"    - **人工修正（HUMAN_REVIEWED）**：{hr['corrected_statement']}")
                        if hr.get("note"):
                            lines.append(f"    - **审定备注**：{hr['note']}")
            lines.append("")

        lines += ["---", "", "本证据矩阵由本地 SQLite 确定性生成，未调用任何外部模型。"]
        return "\n".join(lines).rstrip() + "\n"
