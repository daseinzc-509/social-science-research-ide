"""Real download/verification orchestration using a child interpreter or frozen EXE."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .model_runtime import commit_verified_download, models_directory, state_report
from .network_settings import download_environment

_RUNNING = threading.Lock()


def worker_command(action: str, *, directory: Path, formula: bool) -> list[str]:
    if action not in {"download", "verify"}:
        raise ValueError("Invalid model worker action")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--sra-model-worker", action, str(directory)]
    else:
        command = [sys.executable, "-m", "sociology_research.model_center_worker", action, str(directory)]
    if formula:
        command.append("--formula")
    return command


def _run_worker(action: str, *, directory: Path, formula: bool, progress: Callable[[str], None]) -> None:
    env = download_environment(offline=(action == "verify"))
    if not getattr(sys, "frozen", False):
        src_dir = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    args = worker_command(action, directory=directory, formula=formula)
    # No shell or remote commands; the worker sees only a known local model path.
    with subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as child:
        safe_steps: list[str] = []
        assert child.stdout is not None
        for line in child.stdout:
            # Never forward raw third-party errors/URLs (could contain signed URLs or tokens).
            if line.startswith("SRA stage:"):
                step = line.strip()[:180]
                safe_steps.append(step)
                progress(step)
        code = child.wait()
    if code != 0:
        raise RuntimeError(
            f"Docling model {action} failed (worker exit code {code}). "
            "Check proxy, Hugging Face access, disk space and model availability; "
            "do not publish this build as offline-ready."
        )
    if action == "verify" and "SRA stage: OFFLINE_VERIFIED" not in safe_steps:
        raise RuntimeError("Offline model verification did not report success")


def download_and_verify(*, formula: bool, download: bool, progress: Callable[[str], None]) -> dict:
    if not _RUNNING.acquire(blocking=False):
        raise RuntimeError("A Docling model operation is already running")
    try:
        directory = models_directory()
        directory.mkdir(parents=True, exist_ok=True)
        if download:
            progress("Preparing isolated model download process...")
            _run_worker("download", directory=directory, formula=formula, progress=progress)
        progress("Starting a separate offline verification process...")
        _run_worker("verify", directory=directory, formula=formula, progress=progress)
        commit_verified_download(formula=formula)
        progress("Docling models verified offline; future imports use the local directory.")
        return state_report(formula=formula)
    finally:
        _RUNNING.release()
