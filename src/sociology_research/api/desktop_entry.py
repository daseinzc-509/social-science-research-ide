"""Local, authenticated backend entrypoint for bundled Windows/macOS Desktop.

This file is also the PyInstaller entry script. Nothing here reads or writes user
research content inside the installed application or release build directory.
"""
from __future__ import annotations

import argparse
import ctypes
import hmac
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def token_is_valid(token: str | None) -> bool:
    return isinstance(token, str) and len(token) >= 32 and token.isascii() and token.isprintable()


def is_process_alive(pid: int) -> bool:
    """跨平台检测指定 PID 是否仍存活，用于防桌面宿主异常退出后的孤儿进程残留。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        ERROR_ACCESS_DENIED = 5
        STILL_ACTIVE = 259
        WAIT_TIMEOUT = 0x00000102

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            if exit_code.value != STILL_ACTIVE:
                return False
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def start_parent_watchdog(
    parent_pid: int,
    on_parent_exit: Callable[[], None],
    *,
    poll_interval: float = 1.0,
    alive_check: Callable[[int], bool] = is_process_alive,
    stop_event: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """启动后台守护线程，监测宿主桌面进程退出后立即触发优雅停机回调。"""
    stop = stop_event or threading.Event()
    interval = max(0.05, float(poll_interval))

    def monitor() -> None:
        while not stop.wait(interval):
            # 在 POSIX 平台上若当前进程被 PID 1 收养，直接判定原父进程已消失
            if sys.platform != "win32" and parent_pid != 1 and os.getppid() == 1:
                on_parent_exit()
                return
            if not alive_check(parent_pid):
                on_parent_exit()
                return

    thread = threading.Thread(
        target=monitor,
        daemon=True,
        name=f"sra-parent-watchdog-{parent_pid}",
    )
    thread.start()
    return thread, stop


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
    parser.add_argument("--parent-pid", type=int, default=None)
    args = parser.parse_args(argv)
    if args.port < 1024 or args.port > 65535:
        parser.error("port must be 1024..65535")
    if args.parent_pid is not None and args.parent_pid <= 0:
        parser.error("parent-pid must be a positive integer")
    # Never bind to 0.0.0.0 or ::, even if the parent process has env overrides.
    import uvicorn

    app = create_desktop_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
    server = uvicorn.Server(config)

    watchdog_stop: threading.Event | None = None
    shutdown_lock = threading.Lock()
    exiting = False
    force_exit_timer: threading.Timer | None = None
    if args.parent_pid is not None:
        def trigger_shutdown() -> None:
            nonlocal force_exit_timer
            with shutdown_lock:
                if exiting:
                    return
                server.should_exit = True
                # 若工作线程正阻塞在本地重计算无法及时响应优雅停机，兜底在 5 秒后强退释放资源
                force_exit_timer = threading.Timer(5.0, lambda: os._exit(0))
                force_exit_timer.daemon = True
                force_exit_timer.start()

        _, watchdog_stop = start_parent_watchdog(args.parent_pid, trigger_shutdown)

    try:
        server.run()
    finally:
        with shutdown_lock:
            exiting = True
            if watchdog_stop is not None:
                watchdog_stop.set()
            if force_exit_timer is not None:
                force_exit_timer.cancel()
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
