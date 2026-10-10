"""Experimental lean mode is isolated and fails closed on missing frozen deps.

Only synthetic trees are removed in these tests; no project/user files touched.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_backend, prune_release_assets, frozen_backend_probe  # noqa: E402


def flags(args: list[str], flag: str) -> list[str]:
    return [args[i+1] for i, arg in enumerate(args[:-1]) if arg == flag]


@pytest.fixture
def synthetic_package_tree(tmp_path: Path) -> tuple[Path, Path]:
    backend = tmp_path / 'backend'
    desktop = tmp_path / 'desktop'
    (backend / '_internal/transformers').mkdir(parents=True)
    (backend / '_internal/torch/lib').mkdir(parents=True)
    (desktop).mkdir()
    (backend / 'sra-backend.exe').write_bytes(b'exe')
    (desktop / 'SRA.Desktop.exe').write_bytes(b'exe')
    return backend, desktop


def test_lean_only_narrows_transformers_opt_in(monkeypatch):
    monkeypatch.setattr(build_backend, 'available', lambda name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, 'version', lambda name: '1.2.3')
    normal = build_backend.command_args('full', collection_policy='conservative')
    lean = build_backend.command_args('full', collection_policy='lean')
    compatible = build_backend.command_args('full', collection_policy='compatible')
    assert 'transformers' in flags(normal, '--collect-all')
    assert 'transformers' in flags(compatible, '--collect-all')
    assert 'transformers' not in flags(lean, '--collect-all')
    assert 'transformers' in flags(lean, '--collect-data')
    assert 'transformers' in flags(lean, '--collect-submodules')
    for core in ('docling', 'docling_core', 'docling_parse', 'docling_ibm_models', 'sentencepiece'):
        assert core in flags(lean, '--collect-all')
    for lib in ('transformers', 'keyring', 'huggingface-hub'):
        assert lib in flags(lean, '--copy-metadata')


def test_developer_module_exclusions_only_in_lean(monkeypatch):
    monkeypatch.setattr(build_backend, 'available', lambda name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, 'version', lambda name: '1.2.3')
    normal = build_backend.command_args('full', collection_policy='conservative')
    compatible = build_backend.command_args('full', collection_policy='compatible')
    lean = build_backend.command_args('full', collection_policy='lean')
    assert flags(normal, '--exclude-module') == []
    assert flags(compatible, '--exclude-module') == []
    assert flags(lean, '--exclude-module') == list(build_backend.LEAN_EXCLUDES)
    assert all('torch' not in item and 'docling' not in item and 'cv2' not in item
               for item in build_backend.LEAN_EXCLUDES)


def test_missing_docling_leans_fail_closed(monkeypatch):
    monkeypatch.setattr(build_backend, 'available', lambda name: False)
    with pytest.raises(RuntimeError, match='docling'):
        build_backend.command_args('full', collection_policy='lean')
    with pytest.raises(ValueError, match='collection policy'):
        build_backend.command_args('full', collection_policy='unverified')


def test_type_stubs_not_removed_by_default(synthetic_package_tree):
    backend, desktop = synthetic_package_tree
    keep = backend / '_internal/transformers/module.pyi'
    keep.write_text('def method() -> int: ...', encoding='utf-8')
    assert prune_release_assets.prune(backend, desktop)['candidate_files'] == 0
    assert keep.exists()
    preview = prune_release_assets.prune(backend, desktop, remove_type_stubs=True)
    assert preview['candidate_files'] == 1 and not preview['applied']
    assert keep.exists()


def test_lean_type_stub_pruning_exact_allowlist(synthetic_package_tree):
    backend, desktop = synthetic_package_tree
    remove = backend / '_internal/transformers/module.pyi'
    remove.write_text('class X: ...', encoding='utf-8')
    keep = [
        backend / '_internal/transformers/module.py',
        backend / '_internal/transformers/module.pyd',
        backend / '_internal/transformers/module.pyc',
        backend / '_internal/torch/lib/torch_cpu.dll',
        backend / '_internal/transformers/py.typed',
        backend / '_internal/transformers/weights.bin',
        backend / 'main.pyi',
        desktop / 'Other.pyi',
    ]
    for file in keep:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'runtime')
    result = prune_release_assets.prune(backend, desktop, remove_type_stubs=True, apply=True)
    assert result['candidate_files'] == result['removed_files'] == 1
    assert result['remove_type_stubs']
    assert not remove.exists()
    assert all(file.exists() for file in keep)


def test_real_synthetic_pdf_roundtrip_still_passes():
    image = frozen_backend_probe.pdf_roundtrip()
    assert image[:8] == b'\x89PNG\r\n\x1a\n'
    if importlib.util.find_spec('cv2'):
        frozen_backend_probe.image_decode_roundtrip(image, required=True)


def test_lean_is_opt_in_in_release_workflow():
    release = (ROOT / '.github/workflows/release-desktop.yml').read_text(encoding='utf-8')
    ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'options: [conservative, lean, compatible]' in release
    assert 'default: \'conservative\'' in release
    assert 'policy=conservative' in release  # tags do not default to experiments
    assert 'if [ "$COLLECTION_POLICY" = \'lean\' ]; then' in release
    assert 'set -- "$@" --remove-type-stubs' in release
    assert 'tests/test_lean_collection.py' in ci


def test_frozen_probe_tests_native_libraries_without_network():
    code = (ROOT / 'scripts/frozen_backend_probe.py').read_text(encoding='utf-8')
    for api in ('Tokenizer(', 'AutoConfig.for_model(', 'save_tensor(', 'load_tensor(', 'pdf_roundtrip('):
        assert api in code
    assert 'HF_HUB_OFFLINE' in code
    assert 'TRANSFORMERS_OFFLINE' in code
