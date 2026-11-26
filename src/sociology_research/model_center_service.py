"""Real download/verification orchestration using a child interpreter or frozen EXE."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from . import docling_engine
from .model_runtime import commit_verified_download, models_directory, state_report
from .network_settings import download_environment

_RUNNING = threading.Lock()


def worker_command(action: str, *, directory: Path, formula: bool) -> list[str]:
    if action not in {"download", "verify"}:
        raise ValueError("Invalid model worker action")
    if docling_engine.embedded_docling_version() is None:
        if docling_engine.managed_engine_ready():
            command = [
                str(docling_engine.engine_python_executable()),
                "-m",
                "sociology_research.model_center_worker",
                action,
                str(directory),
            ]
        else:
            raise RuntimeError(
                "尚未安装本地 Docling 解析引擎，无法下载或验证离线权重。"
                "请先在「模型配置中心 → Docling / 增强」点击「一键安装 / 更新 Docling 引擎（CPU 版）」。"
            )
    elif getattr(sys, "frozen", False):
        command = [sys.executable, "--sra-model-worker", action, str(directory)]
    else:
        command = [sys.executable, "-m", "sociology_research.model_center_worker", action, str(directory)]
    if formula:
        command.append("--formula")
    return command


def _run_worker(action: str, *, directory: Path, formula: bool, progress: Callable[[str], None]) -> None:
    env = download_environment(offline=(action == "verify"))
    pkg_root = docling_engine.worker_package_root()
    if pkg_root is not None and (not getattr(sys, "frozen", False) or docling_engine.embedded_docling_version() is None):
        env["PYTHONPATH"] = str(pkg_root) + os.pathsep + env.get("PYTHONPATH", "")
    args = worker_command(action, directory=directory, formula=formula)
    # No shell or remote commands; the worker sees only a known local model path.
    with subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as child:
        safe_steps: list[str] = []
        assert child.stdout is not None
        try:
            for line in child.stdout:
                # Never forward raw third-party errors/URLs (could contain signed URLs or tokens).
                if line.startswith("SRA stage:"):
                    step = line.strip()[:180]
                    safe_steps.append(step)
                    progress(step)
            code = child.wait()
        except BaseException:
            # 任务被取消或出现异常中断时，主动终止子进程避免残留下载/验证孤儿进程
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            raise
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
