"""Safe, idempotent one-time UI polish for the installed-version display.

Remove the obsolete `0.1` from the custom title bar. The real app version
remains discoverable in Settings, backed by the desktop assembly version.

This intentionally edits only two exact AXAML nodes. Refuses to overwrite
an unfamiliar UI, so unrelated local view changes are never replaced.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIEW = ROOT / "desktop" / "SRA.Desktop" / "Views" / "MainWindow.axaml"

TITLE_VERSION = '''            <TextBlock Text="0.1" VerticalAlignment="Center" FontSize="9"
                       Foreground="{DynamicResource InkTertiaryBrush}"/>\n'''
STALE_VERSION = '<TextBlock Grid.Column="1" Text="v0.1.0 · Avalonia"/>'
DYNAMIC_VERSION = '<TextBlock Grid.Column="1" Text="{Binding InstalledDesktopVersion}"/>'


def updated_markup(source: str) -> tuple[str, bool]:
    """Return (updated_text, changed) or raise instead of editing unknown UI."""
    title_count = source.count(TITLE_VERSION)
    if title_count > 1:
        raise ValueError("Ambiguous titlebar version nodes, refusing to edit")
    if title_count == 0 and STALE_VERSION not in source and DYNAMIC_VERSION in source:
        return source, False
    if title_count == 0 and STALE_VERSION not in source:
        raise ValueError("Expected titlebar and settings version nodes not found")
    if STALE_VERSION not in source and DYNAMIC_VERSION not in source:
        raise ValueError("Settings version node not found; preserve the current UI and edit manually")
    # Reject ambiguous mixed / duplicated versions instead of guessing.
    if source.count(STALE_VERSION) > 1 or source.count(DYNAMIC_VERSION) > 1:
        raise ValueError("Ambiguous settings version nodes, refusing to edit")
    result = source.replace(TITLE_VERSION, "", 1)
    result = result.replace(STALE_VERSION, DYNAMIC_VERSION, 1)
    return result, result != source


def patch_view(view: Path, *, write: bool = True) -> bool:
    original = view.read_bytes()
    # Preserve the user's existing LF/CRLF choice to keep Git diffs minimal.
    line_ending = "\r\n" if b"\r\n" in original else "\n"
    old = original.decode("utf-8").replace("\r\n", "\n")
    new, changed = updated_markup(old)
    if changed and write:
        # Saved next to the file and excluded by the repo's typical *.bak rule.
        backup = view.with_name(view.name + ".before_version_polish.bak")
        if backup.exists():
            raise FileExistsError(f"Backup already exists; manually review before rerunning: {backup}")
        shutil.copy2(view, backup)
        view.write_bytes(new.replace("\n", line_ending).encode("utf-8"))
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", type=Path, default=DEFAULT_VIEW)
    parser.add_argument("--check", action="store_true", help="Validate only; do not edit")
    options = parser.parse_args()
    changed = patch_view(options.view, write=not options.check)
    if options.check:
        print("UI VERSION: change pending" if changed else "UI VERSION: already polished")
    else:
        print("UI VERSION: titlebar cleaned; settings now shows installed version" if changed else "UI VERSION: already polished")


if __name__ == "__main__":
    main()
