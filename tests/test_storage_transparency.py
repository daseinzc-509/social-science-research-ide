"""Data locations and uninstall semantics are part of the SRA privacy contract."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from sociology_research import user_storage
from sociology_research.storage_inventory import storage_inventory

BASE = Path(__file__).resolve().parents[1]
DESKTOP = BASE / 'desktop' / 'SRA.Desktop'
ISS = (BASE / 'installer' / 'windows' / 'SRA.Desktop.iss').read_text(encoding='utf-8')
MAC_CLEANUP = (BASE / 'installer' / 'macos' / 'Remove_SRA_User_Data.command').read_text(encoding='utf-8')


def test_inventory_actual_backend_data_path_and_no_side_effects(tmp_path, monkeypatch):
    home = tmp_path / 'sra-private'
    custom = tmp_path / 'legacy-paper-library'
    custom.mkdir()
    (custom / 'research.sqlite3').write_bytes(b'SQLite placeholder')
    (custom / 'paper.pdf').write_bytes(b'%PDF-1.7 fake-for-test')
    monkeypatch.setenv('SRA_HOME', str(home))
    before = sorted(tmp_path.rglob('*'))
    result = storage_inventory(custom)
    assert result['data_dir'] == str(custom.resolve())
    assert result['user_root'] == str(home.resolve())
    assert result['data_managed'] is False
    assert result['usage']['data']['bytes'] == sum(f.stat().st_size for f in custom.iterdir())
    assert sorted(tmp_path.rglob('*')) == before  # GET must not mutate anything


def test_inventory_skips_symlink_target(tmp_path, monkeypatch):
    root = tmp_path / 'home'; private = root / 'data'; private.mkdir(parents=True)
    (private / 'original.txt').write_bytes(b'1234')
    external = tmp_path / 'other'; external.mkdir()
    (external / 'large-secret.txt').write_bytes(b'x' * 1000)
    try:
        (private / 'symlink').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('creating a directory symlink requires privileges on this platform')
    monkeypatch.setenv('SRA_HOME', str(root))
    result = storage_inventory(private)
    assert result['usage']['data']['bytes'] == 4
    assert result['usage']['data']['files'] == 1
    assert result['data_managed'] is True


def test_api_storage_endpoint_exposes_active_data_dir_only(tmp_path, monkeypatch):
    monkeypatch.setenv('SRA_HOME', str(tmp_path / 'sra-user'))
    from fastapi.testclient import TestClient
    from sociology_research.api.app import create_app
    root = tmp_path / 'paper-library'
    app = create_app(data_dir=root)
    with TestClient(app) as client:
        response = client.get('/api/v1/settings/storage')
    assert response.status_code == 200
    payload = response.json()
    assert payload['data_dir'] == str(root.resolve())
    assert payload['data_managed'] is False
    assert 'SRA_API_KEY' not in str(payload)


def test_installer_preserves_silent_user_data_and_requires_two_confirmations():
    assert 'DisableDirPage=no' in ISS
    assert 'DefaultDirName={localappdata}\\Programs\\SRA Desktop' in ISS
    assert 'if UninstallSilent then' in ISS
    assert 'RemovePersonalFiles := False' in ISS
    assert 'MB_YESNOCANCEL' in ISS
    assert 'MB_YESNO' in ISS
    assert 'usPostUninstall' in ISS
    assert "'{localappdata}\\SRA'" in ISS
    assert '[UninstallDelete]' not in ISS
    assert 'DelTree(ExpandConstant(\'{app}\')' not in ISS


def test_mac_cleanup_is_explicit_and_scoped():
    assert 'DELETE-SRA-DATA' in MAC_CLEANUP
    assert 'Quit SRA Desktop' in MAC_CLEANUP
    assert '[[ -L "$root" ]]' in MAC_CLEANUP
    assert '/bin/rm -rf -- "$root"' in MAC_CLEANUP
    assert 'org.daseinzc509.sra.model-credentials' in MAC_CLEANUP
    assert 'SRA_HOME' in MAC_CLEANUP and 'SRA_DATA_DIR' in MAC_CLEANUP
    workflow = (BASE / '.github' / 'workflows' / 'release-desktop.yml').read_text(encoding='utf-8')
    assert 'Remove_SRA_User_Data.command' in workflow


def test_desktop_shows_actual_paths_not_guesswork():
    ui = (DESKTOP / 'Views' / 'MainWindow.axaml').read_text(encoding='utf-8')
    behavior = (DESKTOP / 'Views' / 'MainWindow.axaml.cs').read_text(encoding='utf-8')
    client = (DESKTOP / 'Services' / 'SraApiClient.cs').read_text(encoding='utf-8')
    backend = (DESKTOP / 'Services' / 'LocalBackendHost.cs').read_text(encoding='utf-8')
    for name in ('OpenProgramDir_Click', 'OpenDataDir_Click', 'OpenConfigDir_Click', 'OpenCacheDir_Click', 'RefreshStorage_Click'):
        assert name in ui and name in behavior
    assert 'GetStorageInventoryAsync' in behavior and 'GetStorageInventoryAsync' in client
    assert 'api/v1/settings/storage' in client
    assert 'DesktopStoragePaths.Cache' in client  # PDF viewer copies are SRA-owned
    assert 'HF_HOME' in backend and 'XDG_CACHE_HOME' in backend and 'TORCH_HOME' in backend
    assert 'DOCLING_ARTIFACTS_PATH is intentionally not forced' in backend
