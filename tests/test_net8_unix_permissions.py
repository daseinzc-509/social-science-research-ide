"""Guard the cross-platform code path against a non-existent .NET Directory API."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "desktop" / "SRA.Desktop" / "Services" / "SraApiClient.cs"


def test_temp_pdf_folder_uses_dotnet8_compatible_unix_permissions():
    content = CLIENT.read_text(encoding="utf-8")
    assert "Directory.SetUnixFileMode(" not in content
    assert "new DirectoryInfo(folder).UnixFileMode" in content
    assert "UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute" in content
    assert "if (!OperatingSystem.IsWindows())" in content


def test_release_targets_only_windows_and_apple_silicon():
    for workflow in ("ci.yml", "release-desktop.yml"):
        content = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "win-x64" in content
        assert "osx-arm64" in content
        assert "osx-x64" not in content
        assert "macos-13" not in content
