"""Validate an SRA tag and print version strings for .NET/macOS packaging."""
from __future__ import annotations

import argparse
import re

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9][A-Za-z0-9.-]*))?$")


def parse(tag: str) -> dict[str, str]:
    match = _VERSION.fullmatch(tag)
    if match is None:
        raise ValueError(f"Expected vMAJOR.MINOR.PATCH[-prerelease], got {tag!r}")
    major, minor, patch, suffix = match.groups()
    semantic = f"{int(major)}.{int(minor)}.{int(patch)}" + (f"-{suffix}" if suffix else "")
    return {
        "tag": "v" + semantic,
        "version": semantic,
        "assembly": f"{major}.{minor}.{patch}.0",
        "mac_short": f"{major}.{minor}.{patch}",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--field", choices=("tag", "version", "assembly", "mac_short"), required=True)
    options = parser.parse_args()
    print(parse(options.tag)[options.field])
