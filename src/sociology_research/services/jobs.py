"""In-process background jobs for long local research operations.

The first desktop milestone deliberately keeps jobs in memory.  The job boundary is
stable enough for the Avalonia client, while persistence/recovery can be added later
if real usage shows it is needed.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "error"]
ProgressCallback = Callable[[str], None]
JobTarget = Callable[[ProgressCallback], dict[str, Any]]


@dataclass(slots=True)
class JobState:
    id: str
    kind: str
    status: JobStatus = "queued"
    messages: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class JobService:
    """Thread-safe, process-local job registry used by every application surface."""

    def __init__(self, *, max_messages: int = 300) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._max_messages = max(20, int(max_messages))

    def create(self, kind: str) -> JobState:
        clean_kind = str(kind).strip()
        if not clean_kind:
            raise ValueError("job kind cannot be blank")
        job = JobState(id=str(uuid.uuid4()), kind=clean_kind)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job is not None else None

    def start(self, job: JobState, target: JobTarget) -> None:
        def runner() -> None:
            self._update(
                job.id,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                result = target(lambda message: self.message(job.id, message))
            except Exception as exc:  # boundary intentionally converts failures to job errors
                self._update(
                    job.id,
                    status="error",
                    error=str(exc),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                self._update(
                    job.id,
                    status="done",
                    result=result,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )

        worker = threading.Thread(
            target=runner,
            daemon=True,
            name=f"sra-{job.kind}-{job.id[:8]}",
        )
        worker.start()

    def message(self, job_id: str, message: str) -> None:
        clean = str(message).strip()
        if not clean:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.messages.append(clean)
            if len(job.messages) > self._max_messages:
                del job.messages[: len(job.messages) - self._max_messages]

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for name, value in changes.items():
                setattr(job, name, value)
