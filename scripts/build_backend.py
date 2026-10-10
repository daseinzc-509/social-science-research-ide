"""Build the native SRA Python backend with an auditable collection policy.

Run on each native target runner. Collection policies:
  compatible: keep the previous broad PyInstaller collection rules (rollback).
  conservative: narrow low-risk Python-package collection, but preserve Docling,
                Torch, OCR and Transformers model resources in full releases.

The frozen executable MUST pass --self-test-bundle before assembly. This probe
checks basic PDF, image and (for full profile) model-library imports without
sending files to external services or fetching model weights. It is not a
replacement for end-to-end OCR/layout tests on actual papers.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("core", "full")
POLICIES = ("conservative", "compatible")


def available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _add(args: list[str], module: str, *flags: str) -> None:
    if available(module):
        for flag in flags:
            args += [flag, module]


def command_args(
    profile: str,
    *,
    collection_policy: str = "conservative",
    project: Path = ROOT,
    target: Path | None = None,
) -> list[str]:
    if profile not in PROFILES:
        raise ValueError(f"Invalid backend profile: {profile}")
    if collection_policy not in POLICIES:
        raise ValueError(f"Invalid collection policy: {collection_policy}")
    output = target or (project / "dist" / "backend")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm", "--onedir", "--console", "--noupx",
        "--name", "sra-backend",
        "--paths", str(project / "src"),
        "--paths", str(project / "scripts"),
        "--distpath", str(output),
        "--workpath", str(project / "build" / "pyinstaller"),
        "--specpath", str(project / "build" / "pyinstaller"),
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "multipart",
        "--hidden-import", "frozen_backend_probe",
        "--collect-submodules", "sociology_research.api",
        "--collect-submodules", "sociology_research.services",
    ]
    if collection_policy == "compatible":
        # Matches the earlier collection policy so a failed candidate build
        # can be rebuilt without touching the source or feature set.
        for module in ("keyring", "pymupdf", "fitz", "pydantic", "rapidocr_onnxruntime"):
            _add(args, module, "--collect-all")
    else:
        # PyMuPDF's own hooks and runtime data stay bundled; fitz is the alias
        # package, and Pydantic's Python modules do not need --collect-all data.
        _add(args, "pymupdf", "--collect-all")
        _add(args, "fitz", "--hidden-import")
        _add(args, "pydantic", "--collect-submodules")
        _add(args, "keyring", "--collect-all")  # macOS Keychain backends are dynamic
        _add(args, "rapidocr_onnxruntime", "--collect-all")
    if profile == "full":
        if not available("docling"):
            raise RuntimeError("Full backend build requires docling to be installed")
        # DO NOT trim Docling/Torch/model architecture resources here. Some
        # are dynamically resolved at runtime, after an offline import smoke.
        for module in ("docling", "docling_core", "docling_parse", "docling_ibm_models"):
            _add(args, module, "--collect-all")
        # Transformers lazily imports the vision/RT-DETR processor used by
        # docling-layout-heron. PyInstaller can miss optional torchvision/timm
        # modules despite --collect-all transformers: they live in other
        # distributions. Retain their native binaries and Python modules.
        for module in ("torchvision", "timm"):
            _add(args, module, "--collect-all")
        if collection_policy == "compatible":
            for module in ("huggingface_hub", "transformers", "accelerate", "sentencepiece"):
                _add(args, module, "--collect-all")
        else:
            # hub/accelerate are pure-Python module graphs + package data.
            # Avoid their optional binary/development collections. Keep all
            # Transformers and SentencePiece assets for model compatibility.
            for module in ("huggingface_hub", "accelerate"):
                _add(args, module, "--collect-submodules", "--collect-data")
            for module in ("transformers", "sentencepiece"):
                _add(args, module, "--collect-all")
    # importlib.metadata users require dist-info even with static imports.
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
    parser.add_argument("--profile", choices=PROFILES, default="full")
    parser.add_argument("--collection-policy", choices=POLICIES, default="conservative")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "backend")
    options = parser.parse_args()
    os.environ.setdefault("PYTHONUTF8", "1")
    cmd = command_args(options.profile, collection_policy=options.collection_policy, target=options.output)
    print(f"PyInstaller collection policy: {options.collection_policy}; backend: {options.profile}", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)
    executable = options.output / "sra-backend" / ("sra-backend.exe" if sys.platform == "win32" else "sra-backend")
    if not executable.is_file():
        raise SystemExit(f"Backend executable not produced: {executable}")
    print(f"Backend built: {executable}")


if __name__ == "__main__":
    main()
