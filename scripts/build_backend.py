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
import re
import site
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("full", "slim")
POLICIES = ("conservative", "compatible")

# 瘦壳（slim）模式排除所有重型本地 ML / PDF 推理依赖（torch/docling/transformers/scipy/cv2 等），
# 仅保留轻量 FastAPI + SQLite + Pydantic + pypdfium2 页面渲染；PDF 解析交由模型配置中心一键安装的
# 独立 CPU-only Docling 托管环境（user_root()/engine/docling/venv）隔离执行。
SLIM_HEAVY_EXCLUDES = (
    "docling",
    "docling_core",
    "docling_parse",
    "docling_ibm_models",
    "torch",
    "torchvision",
    "transformers",
    "timm",
    "scipy",
    "numpy",
    "cv2",
    "rapidocr",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "sympy",
    "pandas",
    "accelerate",
    "sentencepiece",
    "huggingface_hub",
    "safetensors",
    "tokenizers",
)

# 桌面无头后端永不使用的测试造数库、单测运行器、Tcl/Tk GUI 与 PythonWin MFC IDE 外壳。
# 注意：绝不包含 torch/torchvision/transformers/cv2/docling/rapidocr/win32/pywin32_system32。
DEV_AND_GUI_EXCLUDES = (
    "tkinter",
    "_tkinter",
    "turtle",
    "turtledemo",
    "idlelib",
    "pytest",
    "_pytest",
    "faker",
    "polyfactory",
    "pythonwin",
)
UNWANTED_BUILD_DISTRIBUTIONS = frozenset({"faker", "polyfactory", "pymupdf"})


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()


def audit_environment_hygiene(site_packages_dirs: Iterable[Path] | None = None) -> dict[str, object]:
    """扫描构建虚拟环境中的重复多版本 .dist-info 残留与开发/遗留包污染。"""
    if site_packages_dirs is None:
        candidates: list[Path] = []
        for entry in [*site.getsitepackages(), *(Path(p) for p in sys.path if p)]:
            path = Path(entry)
            if path.is_dir() and path not in candidates:
                candidates.append(path)
        roots = candidates
    else:
        roots = [Path(p) for p in site_packages_dirs if Path(p).is_dir()]

    by_package: dict[str, list[str]] = {}
    unwanted_found: list[str] = []
    for root in roots:
        for dist_dir in sorted(root.glob("*.dist-info")):
            if not dist_dir.is_dir():
                continue
            stem = dist_dir.name[: -len(".dist-info")]
            if "-" not in stem:
                continue
            pkg_raw, _version = stem.rsplit("-", 1)
            pkg = _normalize_dist_name(pkg_raw)
            if not pkg:
                continue
            by_package.setdefault(pkg, [])
            if dist_dir.name not in by_package[pkg]:
                by_package[pkg].append(dist_dir.name)
            if pkg in UNWANTED_BUILD_DISTRIBUTIONS and dist_dir.name not in unwanted_found:
                unwanted_found.append(dist_dir.name)

    duplicates = {
        pkg: sorted(names)
        for pkg, names in sorted(by_package.items())
        if len(names) > 1
    }
    return {
        "clean": not duplicates and not any(
            _normalize_dist_name(item.rsplit("-", 1)[0]) == "pymupdf" for item in unwanted_found
        ),
        "duplicate_dist_info": duplicates,
        "unwanted_distributions": sorted(unwanted_found),
    }


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
    exclude_dev_gui: bool = False,
    uv_binary: Path | None = None,
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
        "--hidden-import", "sociology_research.model_center_worker",
        "--runtime-hook", str(project / "scripts" / "sra_model_worker_runtime_hook.py"),
        "--collect-submodules", "sociology_research.analysis",
        "--collect-submodules", "sociology_research.api",
        "--collect-submodules", "sociology_research.services",
    ]
    # Docling-only: neither PyMuPDF nor the legacy fitz alias is needed.
    # Excluding their import graphs avoids shipping unrelated MuPDF binaries.
    args += ["--exclude-module", "pymupdf", "--exclude-module", "fitz"]
    if exclude_dev_gui:
        for module in DEV_AND_GUI_EXCLUDES:
            args += ["--exclude-module", module]

    if profile == "slim":
        # 瘦壳模式：排除全部重型 ML 推理库，同时携带纯 Python sociology_research 源码与可选 uv，
        # 供独立托管 Docling 虚拟环境通过子进程直接复用 100% 相同的解析与离线验证逻辑。
        for module in SLIM_HEAVY_EXCLUDES:
            args += ["--exclude-module", module]
        if collection_policy == "compatible":
            for module in ("keyring", "pydantic"):
                _add(args, module, "--collect-all")
        else:
            _add(args, "pydantic", "--collect-submodules")
            _add(args, "keyring", "--collect-all")
        _add(args, "pypdfium2", "--collect-all")
        _add(args, "PIL", "--collect-submodules")
        if available("PIL"):
            args += ["--hidden-import", "PIL.Image"]
        pkg_src = project / "src" / "sociology_research"
        args += ["--add-data", f"{pkg_src}{os.pathsep}sra_src/sociology_research"]
        if uv_binary is not None:
            uv_path = Path(uv_binary).expanduser()
            if sys.platform == "win32" and not uv_path.is_file() and uv_path.with_suffix(".exe").is_file():
                uv_path = uv_path.with_suffix(".exe")
            uv_path = uv_path.resolve()
            if not uv_path.is_file():
                raise FileNotFoundError(f"Specified uv binary not found: {uv_path}")
            args += ["--add-binary", f"{uv_path}{os.pathsep}tools"]
        for module, distribution in (("keyring", "keyring"), ("pydantic", "pydantic")):
            if available(module):
                try:
                    importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    continue
                args += ["--copy-metadata", distribution]
        args += [str(project / "scripts" / "desktop_backend_entry.py")]
        return args

    if collection_policy == "compatible":
        # Rollback mode keeps broad collection for the remaining dependencies.
        for module in ("keyring", "pydantic", "rapidocr_onnxruntime"):
            _add(args, module, "--collect-all")
    else:
        _add(args, "pydantic", "--collect-submodules")
        _add(args, "keyring", "--collect-all")
        _add(args, "rapidocr_onnxruntime", "--collect-all")
    if profile == "full":
        if not available("docling"):
            raise RuntimeError("Full backend build requires docling to be installed")
        if not available("rapidocr"):
            raise RuntimeError(
                "Full backend build requires the rapidocr package used by "
                "Docling OCR (not only rapidocr_onnxruntime)."
            )
        # Critical: RapidOCR loads default_models.yaml relative to its package.
        # Collecting rapidocr_onnxruntime alone does NOT collect this new package.
        # Keep package size bounded: collect the YAML/data and ensure the
        # imported package is included, without grabbing optional OCR backends.
        _add(args, "rapidocr", "--hidden-import", "--collect-data")
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
        "rapidocr": "rapidocr",
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
    parser.add_argument("--exclude-dev-and-gui", action="store_true",
                        help="Exclude test/faker/tkinter/pythonwin modules not used by the headless backend")
    parser.add_argument("--check-clean-env", action="store_true",
                        help="Fail before freezing if duplicate .dist-info metadata or legacy pymupdf remain in site-packages")
    parser.add_argument("--uv-binary", type=Path, default=None,
                        help="Optional path to a standalone uv binary bundled under tools/ in slim profile")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "backend")
    options = parser.parse_args()
    os.environ.setdefault("PYTHONUTF8", "1")
    hygiene = audit_environment_hygiene()
    if hygiene["unwanted_distributions"]:
        print(f"Environment hygiene note - non-runtime distributions present: {hygiene['unwanted_distributions']}", flush=True)
    if options.check_clean_env and not hygiene["clean"]:
        raise SystemExit(
            f"Build environment failed hygiene check: duplicates={hygiene['duplicate_dist_info']}, "
            f"unwanted={hygiene['unwanted_distributions']}"
        )
    cmd = command_args(
        options.profile,
        collection_policy=options.collection_policy,
        exclude_dev_gui=options.exclude_dev_and_gui,
        uv_binary=options.uv_binary,
        target=options.output,
    )
    print(f"PyInstaller collection policy: {options.collection_policy}; backend: {options.profile}", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)
    executable = options.output / "sra-backend" / ("sra-backend.exe" if sys.platform == "win32" else "sra-backend")
    if not executable.is_file():
        raise SystemExit(f"Backend executable not produced: {executable}")
    print(f"Backend built: {executable}")


if __name__ == "__main__":
    main()
