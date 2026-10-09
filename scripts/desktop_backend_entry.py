"""PyInstaller entrypoint: stable file path outside package data."""
import multiprocessing

from sociology_research.api.desktop_entry import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
