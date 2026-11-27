"""Application-level maintenance operations shared by all front ends."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..analyzer import PROMPT_VERSION
from ..backup import create_backup_archive, verify_backup_archive
from ..repository import ResearchRepository


class MaintenanceService:
    def __init__(self, data_dir: Path, repository: ResearchRepository) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.repository = repository

    def doctor(self, *, deep: bool = False) -> dict[str, Any]:
        return self.repository.diagnose(
            self.data_dir / "papers",
            current_prompt_version=PROMPT_VERSION,
            deep=bool(deep),
        )

    def prune_cache(self, *, all_runs: bool, apply: bool, vacuum: bool) -> dict[str, Any]:
        return self.repository.prune_model_runs(
            current_prompt_version=PROMPT_VERSION,
            all_runs=bool(all_runs),
            apply=bool(apply),
            vacuum=bool(vacuum),
        )

    def create_backup_file(self, *, include_pdfs: bool = False) -> tuple[Path, Path, str, dict[str, Any]]:
        """生成临时目录中的 `.zip` 研究库安全备份，返回 (temp_dir, zip_path, download_name, manifest)。"""
        temp_dir = Path(tempfile.mkdtemp(prefix="sra-api-backup-"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = "full" if include_pdfs else "lite"
        download_name = f"sra-library-backup-{suffix}-{stamp}.zip"
        zip_path = temp_dir / download_name
        try:
            manifest = create_backup_archive(self.data_dir, zip_path, include_pdfs=bool(include_pdfs))
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return temp_dir, zip_path, download_name, manifest

    @staticmethod
    def verify_backup_bytes(zip_path: Path) -> dict[str, Any]:
        return verify_backup_archive(zip_path)
