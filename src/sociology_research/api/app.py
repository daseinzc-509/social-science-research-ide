"""FastAPI composition root for the localhost-only SRA API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..analyzer import PaperAnalysisError
from ..llm_client import ModelRequestError
from ..repository import ResearchRepository
from ..services import ApplicationServices, create_application_services
from .routes import analysis, health, imports, jobs, paper_tools, papers, settings, workspace

API_PREFIX = "/api/v1"


def create_app(
    *,
    data_dir: Path | None = None,
    repository: ResearchRepository | None = None,
    services: ApplicationServices | None = None,
) -> FastAPI:
    if services is None:
        resolved_data_dir = Path(
            data_dir or os.environ.get("SRA_DATA_DIR", Path.cwd() / "data")
        ).expanduser().resolve()
        services = create_application_services(resolved_data_dir, repository)

    app = FastAPI(
        title="Social Science Research IDE Local API",
        version="0.3.0",
        description="Local-only API boundary shared by the Avalonia desktop and legacy web UI.",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.services = services

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(papers.router, prefix=API_PREFIX)
    app.include_router(imports.router, prefix=API_PREFIX)
    app.include_router(analysis.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(settings.router, prefix=API_PREFIX)
    app.include_router(paper_tools.router, prefix=API_PREFIX)
    app.include_router(workspace.router, prefix=API_PREFIX)

    @app.exception_handler(KeyError)
    async def handle_key_error(request: Request, exc: KeyError):  # noqa: ARG001
        message = str(exc.args[0]) if exc.args else "not found"
        return JSONResponse(status_code=404, content={"detail": message})

    @app.exception_handler(FileNotFoundError)
    async def handle_file_not_found(request: Request, exc: FileNotFoundError):  # noqa: ARG001
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError):  # noqa: ARG001
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PaperAnalysisError)
    async def handle_analysis_error(request: Request, exc: PaperAnalysisError):  # noqa: ARG001
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ModelRequestError)
    async def handle_model_error(request: Request, exc: ModelRequestError):  # noqa: ARG001
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    return app
