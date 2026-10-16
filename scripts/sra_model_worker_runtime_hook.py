"""PyInstaller runtime hook: dispatch model worker without starting the API."""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "--sra-model-worker":
    from sociology_research.model_center_worker import main
    raise SystemExit(main(sys.argv[2:]))
