"""Packaging guards for frozen Docling and honest batch import outcomes.

Run without Hugging Face downloads or external LLM API calls. The release
workflow separately executes the real *frozen* backend self-test.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_backend, frozen_backend_probe


def _args_pairs(args):
    return {(args[i], args[i + 1]) for i in range(len(args) - 1) if args[i].startswith("--")}


def test_full_build_retains_external_vision_module_graph(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda _name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda _name: "1.0")
    pairs = _args_pairs(build_backend.command_args("full", collection_policy="conservative"))
    assert ("--collect-all", "transformers") in pairs
    assert ("--collect-all", "torchvision") in pairs
    assert ("--collect-all", "timm") in pairs
    assert ("--collect-all", "docling") in pairs
    assert ("--hidden-import", "frozen_backend_probe") in pairs


def test_compatible_build_also_includes_vision(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda _name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda _name: "1.0")
    pairs = _args_pairs(build_backend.command_args("full", collection_policy="compatible"))
    assert ("--collect-all", "torchvision") in pairs
    assert ("--collect-all", "timm") in pairs


def test_core_build_does_not_force_docling_vision(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda _name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda _name: "1.0")
    pairs = _args_pairs(build_backend.command_args("core"))
    assert ("--collect-all", "torchvision") not in pairs
    assert ("--collect-all", "timm") not in pairs


def test_heron_processor_probe_uses_local_config_and_exercises_image(monkeypatch):
    seen = {}

    class FakeProcessor:
        @classmethod
        def from_pretrained(cls, folder, **kwargs):
            seen["kwargs"] = kwargs
            seen["config"] = json.loads((Path(folder) / "preprocessor_config.json").read_text())
            return cls()

        def __call__(self, **kwargs):
            seen["image"] = kwargs["images"]
            seen["tensors"] = kwargs["return_tensors"]
            return {"pixel_values": types.SimpleNamespace(shape=(1, 3, 640, 640))}

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoImageProcessor=FakeProcessor))
    frozen_backend_probe.exercise_heron_image_processor()
    assert seen["config"]["image_processor_type"] == "RTDetrImageProcessor"
    assert seen["kwargs"]["local_files_only"] is True
    assert seen["image"].size == (32, 32)
    assert seen["tensors"] == "pt"


def test_heron_processor_probe_exposes_actionable_failure(monkeypatch):
    class BrokenAutoProcessor:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise ModuleNotFoundError("No module named 'torchvision.transforms'")

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoImageProcessor=BrokenAutoProcessor))
    with pytest.raises(RuntimeError, match="Docling Heron's RT-DETR image processor"):
        frozen_backend_probe.exercise_heron_image_processor()


def test_release_workflow_runs_frozen_processor_probe():
    workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    assert "--self-test-bundle --profile \"$BUNDLE_PROFILE\"" in workflow
    probe = (ROOT / "scripts/frozen_backend_probe.py").read_text(encoding="utf-8")
    assert "exercise_heron_image_processor()" in probe
    assert "AutoImageProcessor.from_pretrained" in probe
    assert 'local_files_only=True' in probe


def test_desktop_batch_import_reports_actual_per_file_outcomes():
    vm = (ROOT / "desktop/SRA.Desktop/ViewModels/MainWindowViewModel.cs").read_text(encoding="utf-8")
    assert "DescribeBatchImport(snapshot, label)" in vm
    assert 'ReadCount("imported")' in vm
    assert 'ReadCount("duplicates")' in vm
    assert 'ReadCount("failed")' in vm
    assert 'throw new InvalidOperationException(summary)' in vm
    assert 'completionMessage(completed)' in vm
    assert 'reasons.Count == 3' in vm


def test_titlebar_version_is_removed_but_about_still_shows_version():
    xaml = (ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")
    assert '<TextBlock Text="0.1"' not in xaml
    assert 'Text="{Binding InstalledDesktopVersion}"' in xaml
