"""进程内后台任务调度引擎，用于承接本地长耗时研究任务。

提供按任务类型划分的有界并发池（避免多个 Docling/PyTorch 解析并发导致内存打满）、
协作式任务取消（排队阶段直接跳过，运行阶段在安全检查点中止）、以及终态任务快照
上限回收机制（避免长时间运行造成内存滞留）。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "error", "cancelled"]
JobLane = Literal["heavy", "analysis", "default"]
ProgressCallback = Callable[[str], None]
JobTarget = Callable[[ProgressCallback], dict[str, Any]]
CleanupCallback = Callable[[], None]

TERMINAL_JOB_STATUSES = frozenset({"done", "error", "cancelled"})
HEAVY_JOB_KINDS = frozenset({
    "import",
    "batch-import",
    "reparse",
    "docling-model-download",
    "docling-model-verify",
})
ANALYSIS_JOB_KINDS = frozenset({"analyze", "batch-analysis"})


class JobCancelled(RuntimeError):
    """任务在安全检查点检测到用户取消请求时抛出的协作式中断异常。"""


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
    cancel_requested: bool = False

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class _BoundedLanePool:
    """按需懒启动守护线程的有界 FIFO 任务执行池。"""

    def __init__(self, name: str, max_workers: int) -> None:
        self.name = name
        self.max_workers = max(1, int(max_workers))
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._lock = threading.Lock()
        self._workers = 0
        self._active = 0

    def submit(self, task: Callable[[], None]) -> None:
        self._queue.put(task)
        with self._lock:
            # 仅当待执行与运行任务超出当前线程数且未达上限时再懒启动新守护线程
            need_worker = self._workers < self.max_workers and (
                self._workers == 0 or self._active + self._queue.qsize() > self._workers
            )
            if need_worker:
                self._workers += 1
                worker_index = self._workers
                thread = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name=f"sra-job-{self.name}-{worker_index}",
                )
                thread.start()

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get()
            with self._lock:
                self._active += 1
            try:
                task()
            finally:
                with self._lock:
                    self._active -= 1
                self._queue.task_done()


class JobService:
    """线程安全的进程内后台任务注册表与有界调度器。"""

    def __init__(
        self,
        *,
        max_messages: int = 300,
        max_retained_jobs: int = 200,
        max_heavy_workers: int = 1,
        max_analysis_workers: int = 2,
        max_default_workers: int = 2,
    ) -> None:
        self._jobs: dict[str, JobState] = {}
        self._versions: dict[str, int] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._cleanups: dict[str, CleanupCallback] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._max_messages = max(20, int(max_messages))
        self._max_retained_jobs = max(20, int(max_retained_jobs))
        self._lanes: dict[JobLane, _BoundedLanePool] = {
            "heavy": _BoundedLanePool("heavy", max_heavy_workers),
            "analysis": _BoundedLanePool("analysis", max_analysis_workers),
            "default": _BoundedLanePool("default", max_default_workers),
        }

    @staticmethod
    def lane_for_kind(kind: str) -> JobLane:
        normalized = str(kind).strip().lower()
        if normalized in HEAVY_JOB_KINDS:
            return "heavy"
        if normalized in ANALYSIS_JOB_KINDS:
            return "analysis"
        return "default"

    def create(self, kind: str) -> JobState:
        clean_kind = str(kind).strip()
        if not clean_kind:
            raise ValueError("job kind cannot be blank")
        job = JobState(id=str(uuid.uuid4()), kind=clean_kind)
        with self._lock:
            self._jobs[job.id] = job
            self._versions[job.id] = 1
            self._cancel_events[job.id] = threading.Event()
            self._prune_finished_locked()
            self._condition.notify_all()
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job is not None else None

    def get_with_version(self, job_id: str) -> tuple[dict[str, Any] | None, int]:
        """原子获取任务当前快照与单调递增修订序号（供 SSE 首帧同步使用）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, 0
            return job.snapshot(), self._versions.get(job_id, 1)

    def wait_for_change(
        self,
        job_id: str,
        *,
        after_version: int = 0,
        timeout: float = 1.0,
    ) -> tuple[dict[str, Any] | None, int]:
        """阻塞等待指定任务版本更新或进入终态，供 SSE 流在工作线程中零轮询唤醒。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                job = self._jobs.get(job_id)
                if job is None:
                    return None, 0
                version = self._versions.get(job_id, 1)
                if version > after_version or job.status in TERMINAL_JOB_STATUSES:
                    return job.snapshot(), version
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return job.snapshot(), version
                self._condition.wait(remaining)

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), self._max_retained_jobs))
        with self._lock:
            # 按创建时间倒序返回近期任务快照
            snapshots = [job.snapshot() for job in reversed(list(self._jobs.values()))]
        return snapshots[:capped]

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(job_id)
            if event is not None:
                return event.is_set()
            job = self._jobs.get(job_id)
            return bool(job is not None and (job.cancel_requested or job.status == "cancelled"))

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        cleanup_to_run: CleanupCallback | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_JOB_STATUSES:
                return job.snapshot()
            already_requested = job.cancel_requested
            job.cancel_requested = True
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = datetime.now(timezone.utc).isoformat()
                self._append_message_locked(job, "Cancelled before execution started.")
                cleanup_to_run = self._cleanups.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
                self._bump_version_locked(job_id)
                self._prune_finished_locked()
            elif not already_requested:
                self._append_message_locked(
                    job,
                    "Cancellation requested; stopping at next safe checkpoint…",
                )
                self._bump_version_locked(job_id)
            snapshot = job.snapshot()

        if cleanup_to_run is not None:
            self._run_cleanup_safely(cleanup_to_run)
        return snapshot

    def start(
        self,
        job: JobState,
        target: JobTarget,
        *,
        lane: JobLane | None = None,
        cleanup: CleanupCallback | None = None,
    ) -> None:
        chosen_lane = lane or self.lane_for_kind(job.kind)
        already_cancelled = False
        pending_cleanup: CleanupCallback | None = None
        with self._lock:
            event = self._cancel_events.setdefault(job.id, threading.Event())
            if cleanup is not None:
                self._cleanups[job.id] = cleanup
            if job.status == "cancelled" or event.is_set():
                # 已在 start 之前被取消：直接执行清理并短路返回，不再入队
                already_cancelled = True
                if job.status != "cancelled":
                    job.status = "cancelled"
                    job.cancel_requested = True
                    job.finished_at = datetime.now(timezone.utc).isoformat()
                pending_cleanup = self._cleanups.pop(job.id, None)
                self._cancel_events.pop(job.id, None)
                self._bump_version_locked(job.id)
                self._prune_finished_locked()

        if already_cancelled:
            if pending_cleanup is not None:
                self._run_cleanup_safely(pending_cleanup)
            return

        def runner() -> None:
            with self._lock:
                current = self._jobs.get(job.id)
                cancel_event = self._cancel_events.get(job.id)
                if current is None:
                    pending = self._cleanups.pop(job.id, None)
                    self._cancel_events.pop(job.id, None)
                    start_allowed = False
                elif current.status == "cancelled" or (cancel_event is not None and cancel_event.is_set()):
                    if current.status != "cancelled":
                        current.status = "cancelled"
                        current.cancel_requested = True
                        current.finished_at = datetime.now(timezone.utc).isoformat()
                        self._bump_version_locked(job.id)
                    pending = self._cleanups.pop(job.id, None)
                    self._cancel_events.pop(job.id, None)
                    self._prune_finished_locked()
                    start_allowed = False
                else:
                    current.status = "running"
                    current.started_at = datetime.now(timezone.utc).isoformat()
                    self._bump_version_locked(job.id)
                    pending = None
                    start_allowed = True

            if not start_allowed:
                if pending is not None:
                    self._run_cleanup_safely(pending)
                return

            def report_progress(message: str) -> None:
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelled("Job cancelled by user.")
                self.message(job.id, message)

            try:
                result = target(report_progress)
            except JobCancelled as exc:
                self._finish(
                    job.id,
                    status="cancelled",
                    error=None,
                    final_message=str(exc) or "Job cancelled by user.",
                )
            except Exception as exc:  # 边界层将业务异常统一转为任务错误状态
                self._finish(
                    job.id,
                    status="error",
                    error=str(exc),
                )
            else:
                self._finish(
                    job.id,
                    status="done",
                    result=result,
                )

        self._lanes[chosen_lane].submit(runner)

    def message(self, job_id: str, message: str) -> None:
        clean = str(message).strip()
        if not clean:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._append_message_locked(job, clean)
            self._bump_version_locked(job_id)

    def _finish(
        self,
        job_id: str,
        *,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        final_message: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = status
                if result is not None:
                    job.result = result
                if error is not None:
                    job.error = error
                job.finished_at = datetime.now(timezone.utc).isoformat()
                if final_message:
                    self._append_message_locked(job, final_message)
                self._bump_version_locked(job_id)
            cleanup_to_run = self._cleanups.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._prune_finished_locked()

        if cleanup_to_run is not None:
            self._run_cleanup_safely(cleanup_to_run)

    def _bump_version_locked(self, job_id: str) -> int:
        if job_id not in self._jobs:
            return 0
        next_ver = self._versions.get(job_id, 1) + 1
        self._versions[job_id] = next_ver
        self._condition.notify_all()
        return next_ver

    def _append_message_locked(self, job: JobState, message: str) -> None:
        clean = str(message).strip()
        if not clean:
            return
        job.messages.append(clean)
        if len(job.messages) > self._max_messages:
            del job.messages[: len(job.messages) - self._max_messages]

    def _prune_finished_locked(self) -> None:
        if len(self._jobs) <= self._max_retained_jobs:
            return
        overflow = len(self._jobs) - self._max_retained_jobs
        removable = [
            job_id
            for job_id, state in self._jobs.items()
            if state.status in TERMINAL_JOB_STATUSES
        ]
        for job_id in removable[:overflow]:
            self._jobs.pop(job_id, None)
            self._versions.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._cleanups.pop(job_id, None)
        if removable[:overflow]:
            self._condition.notify_all()

    @staticmethod
    def _run_cleanup_safely(cleanup: CleanupCallback) -> None:
        try:
            cleanup()
        except Exception:
            # 清理失败不应掩盖原始任务状态或杀死工作线程
            pass
