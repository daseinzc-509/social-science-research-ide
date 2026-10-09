"""Offline release policy checks for BOTH Windows and macOS bundled builds."""
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "SRA.Desktop"
WIN_SETUP = ROOT / "installer" / "windows" / "SRA.Desktop.iss"
CI = ROOT / ".github/workflows/ci.yml"
RELEASE = ROOT / ".github/workflows/release-desktop.yml"


def test_windows_install_uninstall_remain_per_user():
    script = WIN_SETUP.read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script
    assert "AppId=org.daseinzc.socialscienceresearchide.desktop" in script
    assert r"DefaultDirName={localappdata}\Programs\SRA Desktop" in script
    assert "recursesubdirs" in script  # backend is inside installed directory
    assert "[UninstallDelete]" not in script
    assert "AppVersion={#AppVersion}" in script


def test_ci_compiles_supported_platforms_without_heavy_packer_on_every_push():
    workflow = CI.read_text(encoding="utf-8")
    assert "scripts/check_release_safety.py source" in workflow
    for platform in ("win-x64", "osx-arm64"):
        assert platform in workflow
    assert "osx-x64" not in workflow
    assert "macos-26-intel" not in workflow
    assert "dotnet build" in workflow
    assert "test_secure_distribution.py" in workflow
    assert "test_crossplatform_desktop.py" in workflow
    assert "scripts/build_backend.py" not in workflow


def test_release_packages_real_backend_and_blocks_private_files():
    workflow = RELEASE.read_text(encoding="utf-8")
    for platform in ("win-x64", "osx-arm64"):
        assert platform in workflow
    assert "osx-x64" not in workflow
    assert "macos-26-intel" not in workflow
    assert "Verify required Windows and Apple Silicon packages" in workflow
    assert "scripts/build_backend.py" in workflow
    assert "scripts/smoke_backend.py" in workflow
    assert "--self-contained true" in workflow
    assert "scripts/check_release_safety.py source" in workflow
    assert "scripts/check_release_safety.py artifact" in workflow
    assert "scripts/check_release_safety.py zip" in workflow
    assert "installer/macos/package_app.py" in workflow
    assert "hdiutil create" in workflow
    assert "installer/windows/SRA.Desktop.iss" in workflow
    assert "choco install innosetup" in workflow
    assert "unins000.exe" in workflow
    assert "SHA256SUMS" in workflow
    assert "--prerelease" in workflow  # unsigned mac previews not stable releases


def test_update_client_only_discovers_official_release_page():
    update = (DESKTOP / "Services/DesktopUpdateService.cs").read_text(encoding="utf-8")
    assert "daseinzc-509/social-science-research-ide" in update
    assert "ReadAsStreamAsync" in update
    assert "CompareTags" in update
    assert "Process.Start" not in update and "File.WriteAllBytes" not in update
    window = (DESKTOP / "Views/MainWindow.axaml").read_text(encoding="utf-8")
    assert "CheckForUpdates_Click" in window
    assert "OpenUpdateRelease_Click" in window
    ET.parse(DESKTOP / "Views/MainWindow.axaml")


def test_desktop_autostarts_private_backend():
    backend = (DESKTOP / "Services/LocalBackendHost.cs").read_text(encoding="utf-8")
    assert "RandomNumberGenerator.GetBytes(32)" in backend
    assert "IPAddress.Loopback" in backend
    assert 'info.Environment["SRA_DESKTOP_TOKEN"]' in backend
    assert "Kill(entireProcessTree: true)" in backend
    app = (DESKTOP / "App.axaml.cs").read_text(encoding="utf-8")
    assert "LocalBackendHost.Launch()" in app
    assert "_backendHost.ReadyTask" in app
    client = (DESKTOP / "Services/SraApiClient.cs").read_text(encoding="utf-8")
    assert '"X-SRA-Desktop-Token"' in client
    assert "DownloadPdfForOpenAsync" in client


def test_user_prefs_remain_private_and_update_controlled():
    prefs = (DESKTOP / "Services/DesktopPreferences.cs").read_text(encoding="utf-8")
    assert "LocalApplicationData" in prefs
    assert "CheckForUpdatesAtStartup" in prefs
    assert "IncludePrereleaseUpdates" in prefs
    assert "ApiKey" not in prefs
    user_vm = (DESKTOP / "ViewModels/MainWindowViewModel.cs").read_text(encoding="utf-8")
    assert "CheckForUpdatesAsync" in user_vm
    assert "FindUpdateAsync" in user_vm
