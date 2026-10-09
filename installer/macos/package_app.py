"""Produce a Mac .app bundle from self-contained Avalonia + PyInstaller backend.

The GitHub macOS job then codesigns the .app and creates the .dmg.
This script never copies a project's data/, .env, or local model cache.
"""
from __future__ import annotations

import argparse
import plistlib
import shutil
import stat
from pathlib import Path

BUNDLE_ID = "org.daseinzc509.socialscienceresearchide"


def package_app(desktop: Path, backend: Path, output: Path, version: str) -> Path:
    desktop, backend, output = desktop.resolve(), backend.resolve(), output.resolve()
    if not (desktop / "SRA.Desktop").is_file():
        raise ValueError("Missing macOS dotnet self-contained SRA.Desktop executable")
    if not (backend / "sra-backend").is_file():
        raise ValueError("Missing frozen macOS sra-backend executable")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing app bundle: {output}")

    contents = output / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    for item in desktop.iterdir():
        destination = macos / item.name
        if item.is_dir():
            shutil.copytree(item, destination, symlinks=True)
        else:
            shutil.copy2(item, destination)
    shutil.copytree(backend, resources / "backend" / "sra-backend", symlinks=True)
    (macos / "SRA.Desktop").chmod((macos / "SRA.Desktop").stat().st_mode | stat.S_IXUSR)
    executable = resources / "backend" / "sra-backend" / "sra-backend"
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    icon = resources / "sra.icns"
    if not icon.is_file():
        # The CI step generates sra.icns *before* signing. A placeholder icon
        # may be copied separately from installer/macos/make_icon.sh.
        pass
    plist = {
        "CFBundleName": "Social Science Research IDE",
        "CFBundleDisplayName": "Social Science Research IDE",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "SRA.Desktop",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleIconFile": "sra.icns",
        # Only Apple Silicon macOS 14+ packages are maintained.
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.education",
    }
    with (contents / "Info.plist").open("wb") as output_file:
        plistlib.dump(plist, output_file)
    return output


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desktop", type=Path, required=True)
    ap.add_argument("--backend", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--version", required=True)
    args = ap.parse_args()
    print(package_app(args.desktop, args.backend, args.output, args.version))
