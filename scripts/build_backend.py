"""Build the platform-native SRA backend with PyInstaller (no user documents).

Run on each target architecture. macOS binaries cannot be cross-compiled from Windows.
Profile 'full' installs Docling and packaging hooks; 'core' helps debug CI.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def command_args(profile: str, *, project: Path = ROOT, target: Path | None = None) -> list[str]:
    output = target or (project / "dist" / "backend")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm", "--onedir", "--console", "--noupx",
        "--name", "sra-backend",
        "--paths", str(project / "src"),
        "--distpath", str(output),
        "--workpath", str(project / "build" / "pyinstaller"),
        "--specpath", str(project / "build" / "pyinstaller"),
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "multipart",
        "--collect-submodules", "sociology_research.api",
        "--collect-submodules", "sociology_research.services",
    ]
    # Dynamic model/layout/Keychain imports need explicit hooks.
    for module in ("keyring", "pymupdf", "fitz", "pydantic", "rapidocr_onnxruntime"):
        if available(module):
            args += ["--collect-all", module]
    if profile == "full":
        if not available("docling"):
            raise RuntimeError("Full backend build requires docling to be installed")
        # Automatic PyInstaller hooks collect Torch, transformers, Docling parser
        # native libraries. These can make the backend very large.
        for module in (
            "docling", "docling_core", "docling_parse", "docling_ibm_models",
            "huggingface_hub", "transformers", "accelerate", "sentencepiece",
        ):
            if available(module):
                args += ["--collect-all", module]
    # Runtime version lookups and macOS Keychain plugin discovery may use
    # importlib.metadata, which PyInstaller does not infer from normal imports.
    import importlib.metadata
    distributions = {
        "docling": "docling",
        "docling_core": "docling-core",
        "docling_parse": "docling-parse",
        "docling_ibm_models": "docling-ibm-models",
        "transformers": "transformers",
        "huggingface_hub": "huggingface-hub",
        "keyring": "keyring",
    }
    for module, distribution in distributions.items():
        if available(module):
            try:
                importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                continue
            args += ["--copy-metadata", distribution]
    args += [str(project / "scripts" / "desktop_backend_entry.py")]
    return args


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("core", "full"), default="full")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "backend")
    args = parser.parse_args()
    os.environ.setdefault("PYTHONUTF8", "1")
    subprocess.run(command_args(args.profile, target=args.output), check=True, cwd=ROOT)
    executable = args.output / "sra-backend" / ("sra-backend.exe" if sys.platform == "win32" else "sra-backend")
    if not executable.is_file():
        raise SystemExit(f"Backend executable not produced: {executable}")
    print(f"Backend built: {executable}")


if __name__ == "__main__":
    main()
