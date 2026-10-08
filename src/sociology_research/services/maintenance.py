"""Application-level maintenance operations shared by all front ends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..analyzer import PROMPT_VERSION
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
