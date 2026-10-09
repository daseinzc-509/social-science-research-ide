"""Local, authenticated backend entrypoint for bundled Windows/macOS Desktop.

This file is also the PyInstaller entry script. Nothing here reads or writes user
research content inside the installed application or release build directory.
"""
from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def token_is_valid(token: str | None) -> bool:
    return isinstance(token, str) and len(token) >= 32 and token.isascii() and token.isprintable()


class DesktopTokenMiddleware:
    """Guard all HTTP routes, including PDF and settings. No browser CORS bypass."""

    def __init__(self, app, token: str):
        if not token_is_valid(token):
            raise ValueError("SRA desktop session token is missing or too short")
        self.app = app
        self.expected = token.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        provided = next(
            (value for name, value in scope.get("headers", ()) if name.lower() == b"x-sra-desktop-token"),
            b"",
        )
        if not hmac.compare_digest(provided, self.expected):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse({"detail": "Desktop session authentication required"}, status_code=401)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_desktop_app(*, data_dir: Path | None = None, token: str | None = None) -> FastAPI:
    from sociology_research.api.app import create_app
    from sociology_research.user_storage import resolve_data_dir

    secret = token if token is not None else os.environ.get("SRA_DESKTOP_TOKEN")
    if not token_is_valid(secret):
        raise RuntimeError("SRA Desktop requires a per-process session token")
    app = create_app(data_dir=data_dir or resolve_data_dir())
    app.add_middleware(DesktopTokenMiddleware, token=secret)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private localhost service for SRA Desktop")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if args.port < 1024 or args.port > 65535:
        parser.error("port must be 1024..65535")
    # Never bind to 0.0.0.0 or ::, even if the parent process has env overrides.
    import uvicorn

    app = create_desktop_app()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
