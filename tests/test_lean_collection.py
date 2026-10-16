"""Guard the supported, non-destructive PyInstaller collection policies.

The previous experimental `lean` backend was withdrawn; it is intentionally
REJECTED by build_backend.py and must not remain selectable in release.yml.
Only synthetic trees are mutated here, never a project or user directory.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_backend, frozen_backend_probe  # noqa: E402


def flags(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == flag]


@pytest.fixture
def synthetic_package_tree(tmp_path: Path) -> tuple[Path, Path]:
    backend = tmp_path / "backend"
    desktop = tmp_path / "desktop"
    (backend / "_internal/transformers").mkdir(parents=True)
    (backend / "_internal/torch/lib").mkdir(parents=True)
    desktop.mkdir()
    (backend / "sra-backend.exe").write_bytes(b"exe")
    (desktop / "SRA.Desktop.exe").write_bytes(b"exe")
    return backend, desktop


def _mock_installed_dependencies(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda module: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda name: "1.2.3")


def test_supported_policies_keep_docling_and_transformers(monkeypatch):
    _mock_installed_dependencies(monkeypatch)
    assert build_backend.POLICIES == ("conservative", "compatible")
    conservative = build_backend.command_args("full", collection_policy="conservative")
    compatible = build_backend.command_args("full", collection_policy="compatible")
    for args in (conservative, compatible):
        collected = flags(args, "--collect-all")
        for module in (
            "docling", "docling_core", "docling_parse", "docling_ibm_models",
            "torchvision", "timm", "transformers", "sentencepiece",
        ):
            assert module in collected, (module, args)
        assert "rapidocr" in flags(args, "--collect-data")
        assert "rapidocr" in flags(args, "--hidden-import")
        metadata = flags(args, "--copy-metadata")
        for distribution in ("docling", "rapidocr", "transformers", "huggingface-hub"):
            assert distribution in metadata
    for module in ("huggingface_hub", "accelerate"):
        assert module in flags(conservative, "--collect-submodules")
        assert module in flags(conservative, "--collect-data")
        assert module in flags(compatible, "--collect-all")


def test_explicit_exclusions_avoid_native_docling_libraries(monkeypatch):
    _mock_installed_dependencies(monkeypatch)
    for policy in build_backend.POLICIES:
        args = build_backend.command_args("full", collection_policy=policy)
        # The old MuPDF graph is excluded: SRA's Docling parser uses PDFium.
        assert flags(args, "--exclude-module") == ["pymupdf", "fitz"]
        for module in ("torch", "torchvision", "transformers", "cv2", "docling", "rapidocr"):
            assert module not in flags(args, "--exclude-module")
        assert "sociology_research.model_center_worker" in flags(args, "--hidden-import")
        assert len(flags(args, "--runtime-hook")) == 1
        assert flags(args, "--runtime-hook")[0].endswith("sra_model_worker_runtime_hook.py")


def test_retired_lean_and_missing_critical_deps_fail_closed(monkeypatch):
    _mock_installed_dependencies(monkeypatch)
    for unsupported in ("lean", "unverified", ""):
        with pytest.raises(ValueError, match="collection policy"):
            build_backend.command_args("full", collection_policy=unsupported)
    monkeypatch.setattr(build_backend, "available", lambda name: name != "docling")
    with pytest.raises(RuntimeError, match="docling"):
        build_backend.command_args("full", collection_policy="conservative")
    monkeypatch.setattr(build_backend, "available", lambda name: name != "rapidocr")
    with pytest.raises(RuntimeError, match="rapidocr"):
        build_backend.command_args("full", collection_policy="compatible")


def test_type_stubs_not_removed_by_default(synthetic_package_tree):
    from scripts import prune_release_assets

    backend, desktop = synthetic_package_tree
    keep = backend / "_internal/transformers/module.pyi"
    keep.write_text("def method() -> int: ...", encoding="utf-8")
    assert prune_release_assets.prune(backend, desktop)["candidate_files"] == 0
    assert keep.exists()
    preview = prune_release_assets.prune(backend, desktop, remove_type_stubs=True)
    assert preview["candidate_files"] == 1 and not preview["applied"]
    assert keep.exists()


def test_type_stub_pruning_exact_allowlist_still_safe(synthetic_package_tree):
    from scripts import prune_release_assets

    backend, desktop = synthetic_package_tree
    remove = backend / "_internal/transformers/module.pyi"
    remove.write_text("class X: ...", encoding="utf-8")
    keep = [
        backend / "_internal/transformers/module.py",
        backend / "_internal/transformers/module.pyd",
        backend / "_internal/transformers/module.pyc",
        backend / "_internal/torch/lib/torch_cpu.dll",
        backend / "_internal/transformers/py.typed",
        backend / "_internal/transformers/weights.bin",
        backend / "main.pyi",
        desktop / "Other.pyi",
    ]
    for file in keep:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"runtime")
    result = prune_release_assets.prune(backend, desktop, remove_type_stubs=True, apply=True)
    assert result["candidate_files"] == result["removed_files"] == 1
    assert result["remove_type_stubs"]
    assert not remove.exists()
    assert all(file.exists() for file in keep)


def test_real_synthetic_pdf_roundtrip_still_passes():
    image = frozen_backend_probe.pdf_roundtrip()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    if importlib.util.find_spec("cv2"):
        frozen_backend_probe.image_decode_roundtrip(image, required=True)


def test_release_choices_match_the_builders_supported_policies():
    release = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    section = release.split("      collection_policy:", 1)[1].split("  push:", 1)[0]
    expected = "options: [" + ", ".join(build_backend.POLICIES) + "]"
    assert expected in section
    assert "default: 'conservative'" in section
    assert 'case "$policy" in conservative|compatible)' in release
    assert "policy=conservative" in release  # tags must retain conservative
    assert '--collection-policy "$COLLECTION_POLICY"' in release
    assert "lean" not in section.lower()
    assert "--remove-type-stubs" not in release


def test_frozen_probe_exercises_offline_native_libraries():
    code = (ROOT / "scripts/frozen_backend_probe.py").read_text(encoding="utf-8")
    for required in (
        "pdf_roundtrip()", "image_decode_roundtrip(", "verify_rapidocr_model_manifest()",
        "default_models.yaml", "exercise_heron_image_processor()",
        "AutoImageProcessor.from_pretrained", "local_files_only=True",
        'os.environ["HF_HUB_OFFLINE"] = "1"',
        'os.environ["TRANSFORMERS_OFFLINE"] = "1"',
        "import torch", "from docling.document_converter import DocumentConverter",
    ):
        assert required in code, required
    assert "snapshot_download(" not in code
    assert "hf_hub_download(" not in code
