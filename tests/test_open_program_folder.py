"""Contract checks for folder opening in the SRA desktop view.

The actual Windows ShellExecute behavior must be validated on Windows.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WINDOW = ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml.cs"
AXAML = ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml"


def test_program_button_uses_executable_directory():
    code = WINDOW.read_text(encoding="utf-8")
    assert 'OpenFolder(AppContext.BaseDirectory)' in code
    assert 'private void OpenProgramDir_Click' in code
    if AXAML.exists():
        assert 'Click="OpenProgramDir_Click"' in AXAML.read_text(encoding="utf-8")


def test_windows_uses_shell_for_directory_not_explorer_arguments():
    code = WINDOW.read_text(encoding="utf-8")
    method = code.split('private void OpenFolder(string? path)', 1)[1].split('private async void ImportPdf_Click', 1)[0]
    assert 'Path.TrimEndingDirectorySeparator(Path.GetFullPath(path))' in method
    assert 'Directory.Exists(directory)' in method
    assert 'if (OperatingSystem.IsWindows())' in method
    assert 'new ProcessStartInfo(directory)' in method
    assert 'UseShellExecute = true' in method
    assert 'Verb = "open"' in method
    assert '"explorer.exe"' not in method
    assert method.index('Directory.Exists(directory)') < method.index('Process.Start(start)')


def test_unix_folder_opening_and_errors_remain_intact():
    code = WINDOW.read_text(encoding="utf-8")
    method = code.split('private void OpenFolder(string? path)', 1)[1].split('private async void ImportPdf_Click', 1)[0]
    assert 'OperatingSystem.IsMacOS() ? "open" : "xdg-open"' in method
    assert 'start.ArgumentList.Add(directory)' in method
    assert 'UseShellExecute = false' in method
    assert '目录尚未创建：' in method
    assert '无法打开目录：' in method
    assert 'Process.Start(start)' in method
