"""Merge dotnet publish and a frozen backend into one per-user Windows installer tree."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def assemble(desktop: Path, backend: Path, output: Path) -> Path:
    desktop, backend, output = desktop.resolve(), backend.resolve(), output.resolve()
    if not (desktop / "SRA.Desktop.exe").is_file():
        raise ValueError("Expected self-contained SRA.Desktop.exe in dotnet publish output")
    if not (backend / "sra-backend.exe").is_file():
        raise ValueError("Expected PyInstaller onedir backend/sra-backend.exe")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite combined build: {output}")
    shutil.copytree(desktop, output)
    shutil.copytree(backend, output / "backend" / "sra-backend")
    return output


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desktop", type=Path, required=True)
    ap.add_argument("--backend", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    print(assemble(args.desktop, args.backend, args.output))
