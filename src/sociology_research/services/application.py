"""Composition root for the local application-service layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..analyzer import PaperAnalysisPipeline
from ..pipeline import ImportPipeline
from ..repository import ResearchRepository
from .analysis import AnalysisService
from .exports import ExportService
from .jobs import JobService
from .maintenance import MaintenanceService
from .model_center import ModelCenterService
from .papers import PaperService
from .region_review_service import RegionReviewService
from .settings import SettingsService
from .visual import VisualService


@dataclass(slots=True)
class ApplicationServices:
    data_dir: Path
    repository: ResearchRepository
    jobs: JobService
    papers: PaperService
    analysis: AnalysisService
    settings: SettingsService
    exports: ExportService
    maintenance: MaintenanceService
    visual: VisualService
    region_review: RegionReviewService
    model_center: ModelCenterService


def create_application_services(
    data_dir: Path,
    repository: ResearchRepository | None = None,
    *,
    pipeline_factory: Callable[..., PaperAnalysisPipeline] = PaperAnalysisPipeline,
    import_pipeline_factory: Callable[..., ImportPipeline] = ImportPipeline,
) -> ApplicationServices:
    resolved_data_dir = Path(data_dir).expanduser().resolve()
    repo = repository or ResearchRepository(resolved_data_dir / "research.sqlite3")
    jobs = JobService()
    papers = PaperService(
        resolved_data_dir,
        repo,
        jobs,
        import_pipeline_factory=import_pipeline_factory,
    )
    return ApplicationServices(
        data_dir=resolved_data_dir,
        repository=repo,
        jobs=jobs,
        papers=papers,
        analysis=AnalysisService(
            repo,
            jobs,
            pipeline_factory=pipeline_factory,
        ),
        settings=SettingsService(),
        exports=ExportService(repo),
        maintenance=MaintenanceService(resolved_data_dir, repo),
        visual=VisualService(repo, papers),
        region_review=RegionReviewService(repo, papers),
        model_center=ModelCenterService(jobs),
    )
