"""Keep the titlebar free of stale hard-coded version labels."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.apply_titlebar_version import DYNAMIC_VERSION, STALE_VERSION, TITLE_VERSION, patch_view, updated_markup  # noqa: E402


SAMPLE = "<Window>\n" + TITLE_VERSION + '\n' + STALE_VERSION + '\n<TextBlock Text="Do not touch"/>\n</Window>\n'


def test_removes_only_title_version_and_updates_settings_version():
    result, changed = updated_markup(SAMPLE)
    assert changed
    assert TITLE_VERSION not in result
    assert STALE_VERSION not in result
    assert DYNAMIC_VERSION in result
    assert '<TextBlock Text="Do not touch"/>' in result
    assert updated_markup(result) == (result, False)


def test_never_changes_unfamiliar_views():
    from pytest import raises
    with raises(ValueError, match="Expected titlebar"):
        updated_markup("<Window><TextBlock Text='other'/></Window>")
    with raises(ValueError, match="Ambiguous"):
        updated_markup(SAMPLE + STALE_VERSION)


def test_updates_file_once_and_backs_up_previous_version(tmp_path):
    target = tmp_path / "MainWindow.axaml"
    target.write_text(SAMPLE, encoding="utf-8")
    assert patch_view(target, write=False)
    assert target.read_text(encoding="utf-8") == SAMPLE
    assert patch_view(target)
    backup = tmp_path / "MainWindow.axaml.before_version_polish.bak"
    assert backup.read_text(encoding="utf-8") == SAMPLE
    assert not patch_view(target)


def test_crlf_line_endings_are_preserved(tmp_path):
    target = tmp_path / "MainWindow.axaml"
    target.write_bytes(SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    assert patch_view(target)
    assert b"\r\n" in target.read_bytes()
    assert b"\r\n" in (tmp_path / "MainWindow.axaml.before_version_polish.bak").read_bytes()


def test_project_titlebar_is_current_version_free():
    from xml.etree import ElementTree
    view = ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml"
    if not view.exists():
        import pytest
        pytest.skip("Run this check after merging into the full SRA repository")
    markup = view.read_text(encoding="utf-8")
    ElementTree.fromstring(markup)  # keep a valid Avalonia XML tree
    assert TITLE_VERSION not in markup
    assert STALE_VERSION not in markup
    assert DYNAMIC_VERSION in markup
