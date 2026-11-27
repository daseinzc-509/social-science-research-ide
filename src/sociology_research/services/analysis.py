"""Application service for Paper Understanding analysis workflows."""

from __future__ import annotations

from typing import Any, Callable

from ..analyzer import PaperAnalysisPipeline
from ..repository import ResearchRepository
from .jobs import JobCancelled, JobService


class AnalysisService:
    def __init__(
        self,
        repository: ResearchRepository,
        jobs: JobService,
        *,
        pipeline_factory: Callable[..., PaperAnalysisPipeline] = PaperAnalysisPipeline,
    ) -> None:
        self.repository = repository
        self.jobs = jobs
        self.pipeline_factory = pipeline_factory

    def preview(self, paper_id: str, *, exclude_after_text: str | None = None) -> dict[str, object]:
        self._require_paper(paper_id)
        pipeline = self.pipeline_factory(self.repository)
        return pipeline.preview(
            paper_id,
            exclude_after_text=(exclude_after_text or "").strip() or None,
        )

    def start_analysis(
        self,
        paper_id: str,
        *,
        research_context: str | None = None,
        exclude_after_text: str | None = None,
        force: bool = False,
    ) -> str:
        self._require_paper(paper_id)
        job = self.jobs.create("analyze")

        def work(progress):
            progress("Preparing analysis…")
            pipeline = self.pipeline_factory(self.repository, progress=progress)
            card = pipeline.analyze(
                paper_id,
                research_context=(research_context or "").strip() or None,
                exclude_after_text=(exclude_after_text or "").strip() or None,
                force=bool(force),
            )
            persisted = self.repository.get_card(paper_id)
            if persisted is None:
                raise RuntimeError("analysis returned but the Paper Card was not persisted")
            return {
                "paper_id": card.paper_id,
                "persisted": True,
                "facts": len(card.basic_facts),
                "claim_audits": len(card.claim_audits),
                "evidence_spans": len(card.evidence_spans),
                "tables": len(card.tables),
                "analysis_claims": len(card.analysis),
                "warnings": len(card.warnings),
                "study_type": card.study_profile.study_type.value if card.study_profile else "unknown",
            }

        self.jobs.start(job, work)
        return job.id

    def start_batch_analysis(
        self,
        paper_ids: list[str] | None = None,
        *,
        project_id: str | None = None,
    ) -> str:
        clean_project = (project_id or "").strip() or None
        if paper_ids:
            selected = list(paper_ids)
        elif clean_project is not None:
            if self.repository.get_project(clean_project) is None:
                raise KeyError(f"unknown project id: {clean_project}")
            entries = self.repository.list_project_papers(clean_project)
            selected = [
                entry.paper_id
                for entry in entries
                if self.repository.get_card(entry.paper_id) is None
            ]
            if not selected:
                raise ValueError("no pending papers in this project")
        else:
            selected = [
                paper.id for paper in self.repository.list_papers() if self.repository.get_card(paper.id) is None
            ]
        if not selected:
            raise ValueError("no papers are waiting for analysis")
        job = self.jobs.create("batch-analysis")

        def work(progress):
            done = 0
            failed: list[dict[str, str]] = []
            for index, paper_id in enumerate(selected, start=1):
                paper = self.repository.get_paper(paper_id)
                if paper is None:
                    failed.append({"paper_id": paper_id, "error": "unknown_paper"})
                    continue
                progress(f"Batch analysis {index}/{len(selected)}: {paper.original_name}")
                try:
                    self.pipeline_factory(self.repository, progress=progress).analyze(paper_id)
                    done += 1
                except JobCancelled:
                    raise
                except Exception as exc:
                    failed.append({"paper_id": paper_id, "error": str(exc)})
                    progress(f"Failed: {paper.original_name}: {exc}")
            return {
                "selected": len(selected),
                "completed": done,
                "failed": failed,
                "project_id": clean_project,
            }

        self.jobs.start(job, work)
        return job.id

    def _require_paper(self, paper_id: str) -> None:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
