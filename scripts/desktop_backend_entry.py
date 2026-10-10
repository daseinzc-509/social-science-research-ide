"""PyInstaller entrypoint: private HTTP service plus offline build self-check."""
from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test-bundle":
        # Keep the runtime probe inside the same executable we ship. Importing
        # the source Python instead would not catch missing frozen dependencies.
        from frozen_backend_probe import main as run_probe
        return run_probe(sys.argv[2:])
    from sociology_research.api.desktop_entry import main as run_api
    return run_api()


if __name__ == "__main__":
    raise SystemExit(main())
