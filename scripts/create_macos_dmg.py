"""Build an SRA macOS DMG reliably on GitHub Actions.

First try the standard hdiutil -srcfolder command with bounded retries for
transient EBUSY. If EBUSY persists, use a private writable image with a
private mountpoint, then convert it to the final compressed DMG. Never unmount
or remove other applications' volumes and never copy user data.

This tool only uses commands included with macOS (hdiutil, ditto, sync).
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MIB = 1024 * 1024


class DmgCommandError(RuntimeError):
    def __init__(self, args: list[str], completed: subprocess.CompletedProcess[str]):
        self.args_list = args
        self.exit_code = completed.returncode
        self.output = "\n".join(value for value in (completed.stdout, completed.stderr) if value)
        super().__init__(f"Command failed ({self.exit_code}): {' '.join(args)}\n{self.output}")

    @property
    def busy(self) -> bool:
        return "resource busy" in self.output.casefold() or "resource temporarily unavailable" in self.output.casefold()


def run(args: list[str]) -> str:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise DmgCommandError(args, completed)
    return completed.stdout


def _sleep(attempt: int, wait: float) -> None:
    if wait:
        time.sleep(min(20.0, wait * attempt))


def _create_direct(source: Path, scratch: Path, volname: str, *, retries: int, wait: float) -> Path | None:
    """Use the fast hdiutil source-folder path, retrying only transient busy failures."""
    for attempt in range(1, retries + 1):
        temp_dmg = scratch / f"direct-{attempt}.dmg"
        try:
            run([
                "hdiutil", "create", "-volname", volname,
                "-srcfolder", str(source), "-format", "UDZO",
                str(temp_dmg),
            ])
            if not temp_dmg.is_file() or temp_dmg.stat().st_size == 0:
                raise RuntimeError("hdiutil returned success but did not create a nonempty DMG")
            return temp_dmg
        except DmgCommandError as exc:
            if not exc.busy:
                raise
            print(f"[SRA DMG] hdiutil create Resource busy ({attempt}/{retries}).", file=sys.stderr)
            if attempt < retries:
                _sleep(attempt, wait)
    return None


def _capacity_megabytes(source: Path) -> int:
    """Allow space for payload data, filesystem metadata and HFS+ overhead."""
    total = 0
    for child in source.rglob("*"):
        if child.is_symlink():
            continue  # Do not follow Applications -> /Applications or framework symlinks.
        if child.is_file():
            total += child.stat().st_size
    return max(256, math.ceil((total * 1.25 + 256 * MIB) / MIB))


def _detach_private(mount: Path, *, wait: float) -> None:
    """Detach ONLY our own temporary mount. Retry normally before force."""
    for attempt in range(1, 6):
        try:
            run(["hdiutil", "detach", str(mount)])
            return
        except DmgCommandError as exc:
            if not exc.busy:
                raise
            if attempt < 5:
                print(f"[SRA DMG] Private image detach busy ({attempt}/5); retrying.", file=sys.stderr)
                _sleep(attempt, wait)
    # We own this private temporary image. Use force only AFTER the copy and sync.
    print("[SRA DMG] Detaching our private staging image with -force.", file=sys.stderr)
    run(["hdiutil", "detach", "-force", str(mount)])


def _create_from_writable_image(source: Path, scratch: Path, volname: str, *, wait: float) -> Path:
    """Fallback avoids hdiutil's implicit -srcfolder mount and uses an isolated mountpoint."""
    rw_image = scratch / "private-staging.dmg"
    mount = scratch / "private-mount"
    mount.mkdir()
    run([
        "hdiutil", "create", "-size", f"{_capacity_megabytes(source)}m",
        "-fs", "HFS+", "-volname", volname,
        "-format", "UDRW", str(rw_image),
    ])
    attached = False
    try:
        # A unique, private mountpoint avoids collisions under /Volumes.
        run(["hdiutil", "attach", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(rw_image)])
        attached = True
        run(["ditto", str(source), str(mount)])
        run(["sync"])
        _detach_private(mount, wait=wait)
        attached = False
    finally:
        if attached:
            try:
                _detach_private(mount, wait=wait)
            except DmgCommandError as exc:
                print(f"[SRA DMG] Unable to detach private staging image: {exc}", file=sys.stderr)
                raise

    compressed = scratch / "from-staging.dmg"
    run(["hdiutil", "convert", str(rw_image), "-format", "UDZO", "-o", str(compressed)])
    if not compressed.is_file() or compressed.stat().st_size == 0:
        raise RuntimeError("hdiutil convert returned success but did not create a nonempty DMG")
    return compressed


def create_dmg(source: Path, output: Path, *, volname: str = "SRA Desktop", retries: int = 4, wait: float = 3.0) -> Path:
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if not source.is_dir() or not (source / "SRA.app").is_dir():
        raise ValueError("DMG source must contain an assembled SRA.app directory")
    if not (source / "Applications").is_symlink():
        raise ValueError("DMG source must include the /Applications drag-and-drop symlink")
    if retries < 1 or retries > 10 or wait < 0:
        raise ValueError("Invalid retry configuration")
    if output.suffix.lower() != ".dmg":
        raise ValueError("Output must be a .dmg file")
    if source == output or source in output.parents:
        raise ValueError("Output DMG cannot be written inside the source tree")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing release DMG: {output}")
    if not output.parent.is_dir():
        raise FileNotFoundError(f"Output parent directory does not exist: {output.parent}")

    with tempfile.TemporaryDirectory(prefix=".sra-dmg-", dir=output.parent) as tmp:
        scratch = Path(tmp)
        image = _create_direct(source, scratch, volname, retries=retries, wait=wait)
        if image is None:
            print("[SRA DMG] Source-folder creation stayed busy; using isolated writable-image fallback.", file=sys.stderr)
            image = _create_from_writable_image(source, scratch, volname, wait=wait)
        run(["hdiutil", "verify", str(image)])
        # Rename on the same volume only after verification. Never leave a partial published DMG.
        os.replace(image, output)
    print(f"[SRA DMG] Created and verified: {output} ({output.stat().st_size / MIB:.2f} MiB)")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srcfolder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--volname", default="SRA Desktop")
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--wait-seconds", type=float, default=3.0)
    args = parser.parse_args()
    create_dmg(args.srcfolder, args.output, volname=args.volname, retries=args.retries, wait=args.wait_seconds)


if __name__ == "__main__":
    main()
