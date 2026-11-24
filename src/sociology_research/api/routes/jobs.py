from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ...services.application import ApplicationServices
from ...services.jobs import TERMINAL_JOB_STATUSES
from ..dependencies import get_services
from ..schemas import JobListResponse, JobSnapshot

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _format_sse_snapshot(snapshot: dict[str, Any], version: int) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False)
    return f"id: {version}\nevent: snapshot\ndata: {payload}\n\n"


@router.get("", response_model=JobListResponse)
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    services: ApplicationServices = Depends(get_services),
):
    return {"jobs": services.jobs.list_jobs(limit=limit)}


@router.get("/{job_id}", response_model=JobSnapshot)
def get_job(job_id: str, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    heartbeat_seconds: float = Query(default=15.0, ge=0.1, le=60.0),
    services: ApplicationServices = Depends(get_services),
):
    """基于 Server-Sent Events (SSE) 实时推送任务状态与进度日志，终态自动闭合。"""
    initial_snapshot, initial_version = services.jobs.get_with_version(job_id)
    if initial_snapshot is None:
        raise HTTPException(status_code=404, detail="unknown job")

    async def event_generator():
        last_version = initial_version
        yield _format_sse_snapshot(initial_snapshot, last_version)
        if initial_snapshot.get("status") in TERMINAL_JOB_STATUSES:
            return

        wait_slice = min(float(heartbeat_seconds), 0.5)
        last_emit = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            snapshot, version = await asyncio.to_thread(
                services.jobs.wait_for_change,
                job_id,
                after_version=last_version,
                timeout=wait_slice,
            )
            if snapshot is None:
                return
            if version > last_version or snapshot.get("status") in TERMINAL_JOB_STATUSES:
                last_version = version
                last_emit = time.monotonic()
                yield _format_sse_snapshot(snapshot, last_version)
                if snapshot.get("status") in TERMINAL_JOB_STATUSES:
                    return
            elif time.monotonic() - last_emit >= heartbeat_seconds:
                last_emit = time.monotonic()
                yield ": keep-alive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, private",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{job_id}/cancel", response_model=JobSnapshot)
def cancel_job(job_id: str, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job
