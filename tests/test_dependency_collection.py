"""Conservative PyInstaller collection, Windows video-codec guard and offline probe.

All fixtures are synthetic and never load external model weights or user PDF
files. The real frozen executable is probed in the Release workflow as well.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_backend
from scripts import frozen_backend_probe
from scripts import prune_release_assets


def _options(args):
    return {(args[i], args[i + 1]) for i in range(len(args) - 1) if args[i].startswith("--")}


def test_default_collection_is_conservative_and_preserves_heavy_essentials(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda name: "1.0")
    args = build_backend.command_args("full")
    pairs = _options(args)
    for module in ("pymupdf", "docling", "docling_parse", "docling_core", "docling_ibm_models", "transformers", "sentencepiece"):
        assert ("--collect-all", module) in pairs
    assert ("--collect-all", "fitz") not in pairs
    assert ("--hidden-import", "fitz") in pairs
    assert ("--collect-all", "pydantic") not in pairs
    assert ("--collect-submodules", "pydantic") in pairs
    assert ("--collect-all", "huggingface_hub") not in pairs
    assert ("--collect-submodules", "huggingface_hub") in pairs
    assert ("--collect-data", "huggingface_hub") in pairs
    assert ("--collect-all", "accelerate") not in pairs
    assert ("--collect-data", "accelerate") in pairs
    assert ("--hidden-import", "frozen_backend_probe") in pairs
    assert ("--copy-metadata", "transformers") in pairs
    assert ("--copy-metadata", "keyring") in pairs


def test_compatible_policy_keeps_original_broad_collect_flags(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda name: "1.0")
    args = build_backend.command_args("full", collection_policy="compatible")
    pairs = _options(args)
    for module in ("fitz", "pydantic", "huggingface_hub", "accelerate", "transformers", "sentencepiece"):
        assert ("--collect-all", module) in pairs


def test_missing_docling_and_unsupported_flags_fail_closed(monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda name: False)
    with pytest.raises(RuntimeError, match="docling"):
        build_backend.command_args("full")
    with pytest.raises(ValueError, match="collection policy"):
        build_backend.command_args("core", collection_policy="dangerous")
    with pytest.raises(ValueError, match="profile"):
        build_backend.command_args("missing")
    assert "--onedir" in build_backend.command_args("core")


def _release_tree(tmp_path):
    backend, desktop = tmp_path / "backend", tmp_path / "desktop"
    (backend / "_internal" / "cv2").mkdir(parents=True)
    desktop.mkdir()
    (backend / "sra-backend.exe").write_bytes(b"fake frozen backend")
    (desktop / "SRA.Desktop.exe").write_bytes(b"fake desktop")
    return backend, desktop


def test_video_prune_is_opt_in_and_exactly_scoped(tmp_path):
    backend, desktop = _release_tree(tmp_path)
    codec = backend / "_internal/cv2/opencv_videoio_ffmpeg500_64.dll"
    codec.write_bytes(b"v" * 89)
    keep = [
        backend / "_internal/cv2/cv2.pyd",
        backend / "_internal/cv2/cv2.dll",
        backend / "_internal/opencv_videoio_ffmpeg500_64.dll",
        backend / "_internal/cv2/custom_video.dll",
        backend / "_internal/cv2/opencv_videoio_ffmpeg500_x86.dll",
        backend / "_internal/torch/lib/torch_cpu.dll",
        backend / "_internal/docling/model.dll",
    ]
    for file in keep:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"keep")
    assert prune_release_assets.prune(backend, desktop)["candidate_files"] == 0
    preview = prune_release_assets.prune(backend, desktop, remove_opencv_video=True)
    assert not preview["applied"] and preview["candidate_bytes"] == 89
    assert codec.exists()
    result = prune_release_assets.prune(backend, desktop, apply=True, remove_opencv_video=True)
    assert result["removed_files"] == 1 and result["remove_opencv_video"]
    assert not codec.exists()
    assert all(file.exists() for file in keep)


def test_mac_cli_refuses_windows_video_prune(tmp_path):
    backend, desktop = _release_tree(tmp_path)
    cmd = [
        sys.executable, str(ROOT / "scripts/prune_release_assets.py"),
        "--target", "osx-arm64", "--backend", str(backend),
        "--desktop", str(desktop), "--remove-opencv-video-codecs",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0
    assert "only supported for win-x64" in result.stderr


def test_generated_pdf_can_be_read_and_rasterized_without_models():
    png = frozen_backend_probe.pdf_roundtrip()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    frozen_backend_probe.image_decode_roundtrip(png, required=False)


def test_release_workflow_uses_bundled_probe_before_assembly():
    release = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "collection_policy:" in release
    assert "options: [conservative, lean, compatible]" in release
    assert "--collection-policy \"$COLLECTION_POLICY\"" in release
    assert "--remove-opencv-video-codecs" in release
    assert "--self-test-bundle --profile \"$BUNDLE_PROFILE\"" in release
    assert release.index("--self-test-bundle") < release.index("Assemble complete Windows payload")
    assert release.index("--self-test-bundle") < release.index("package_app.py")
    assert "tests/test_dependency_collection.py" in ci
    assert "sra-backend.exe" in (ROOT / "scripts/desktop_backend_entry.py").read_text(encoding="utf-8") or "--self-test-bundle" in (ROOT / "scripts/desktop_backend_entry.py").read_text(encoding="utf-8")


def _release_step_script(name: str) -> str:
    """Read the exact workflow shell, avoiding dependence on a YAML library."""
    lines = (ROOT / '.github/workflows/release-desktop.yml').read_text(encoding='utf-8').splitlines()
    marker = f'      - name: {name}'
    assert marker in lines, f'Missing workflow step: {name}'
    position = lines.index(marker) + 1
    while position < len(lines) and lines[position].strip() != 'run: |':
        position += 1
    assert position < len(lines), f'Missing run script for {name}'
    script = []
    for line in lines[position + 1:]:
        if line and not line.startswith('          '):
            break
        script.append(line[10:] if line.startswith('          ') else '')
    return '\n'.join(script) + '\n'


def test_tag_release_initializes_collection_policy():
    """A tag push has no workflow_dispatch inputs; it must set its own policy."""
    shell = _release_step_script('Validate release version + determine backend profile')
    push_branch = shell.split('if [ "$TRIGGER" = "push" ]; then', 1)[1].split('else', 1)[0]
    assert 'profile=full' in push_branch
    assert 'policy=conservative' in push_branch


@pytest.mark.parametrize('target, policy, needs_video_flag', [
    ('win-x64', 'conservative', True),
    ('win-x64', 'compatible', False),
    ('osx-arm64', 'conservative', False),
    ('osx-arm64', 'compatible', False),
    ('win-x64', 'lean', True),
    ('osx-arm64', 'lean', False),
])
def test_prune_step_no_empty_array_under_nounset(tmp_path, target, policy, needs_video_flag):
    """Execute the real workflow fragment with a stub, under strict Bash mode.

    Windows developers may not have Bash installed; the same simulation runs
    for all platforms in GitHub's Ubuntu CI runner.
    """
    import json
    import shutil

    if shutil.which('bash') is None:
        pytest.skip('Bash is not installed (runs in Linux CI)')
    step = _release_step_script('Safely prune release-only debug/development files and smoke-test backend')
    assert 'prune_flags[@]' not in step
    assert 'set --' in step and '"$@"' in step
    # The segment after the prune invocation needs the actual frozen binary;
    # use a stub here to test CLI arguments, not the heavyweight runtime.
    fragment = step.split('\ncat "dist/build-size-audit/', 1)[0]
    assert 'python scripts/prune_release_assets.py "$@"' in fragment
    fragment = fragment.replace('${{ matrix.target }}', target)
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (scripts / 'prune_release_assets.py').write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "Path('prune-args.json').write_text(json.dumps(sys.argv[1:]))\n",
        encoding='utf-8',
    )
    process = subprocess.run(
        ['bash', '-c', fragment],
        cwd=tmp_path,
        env={**os.environ, 'COLLECTION_POLICY': policy},
        capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    args = json.loads((tmp_path / 'prune-args.json').read_text(encoding='utf-8'))
    assert args == [
        '--target', target,
        '--backend', 'dist/backend/sra-backend',
        '--desktop', 'dist/desktop',
        '--output-dir', 'dist/build-size-audit',
        '--apply',
    ] + (['--remove-opencv-video-codecs'] if needs_video_flag else []) + (['--remove-type-stubs'] if policy == 'lean' else [])
