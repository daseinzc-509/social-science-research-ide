"""Run a real frozen backend and verify loopback HTTP+auth, with isolated data."""
from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(url, token=None):
    headers = {"X-SRA-Desktop-Token": token} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def smoke(executable: Path, *, timeout: float = 180.0) -> None:
    executable = executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with tempfile.TemporaryDirectory(prefix="sra-bundle-smoke-") as home:
        token = secrets.token_hex(32)
        port = free_port()
        env = os.environ.copy()
        env["SRA_HOME"] = home
        env["SRA_DESKTOP_TOKEN"] = token
        env.pop("SRA_DATA_DIR", None)
        env.pop("SRA_LITE_API_KEY", None)
        env.pop("SRA_PRO_API_KEY", None)
        env.pop("SRA_API_KEY", None)
        child = subprocess.Popen(
            [str(executable), "--port", str(port)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=executable.parent,
        )
        try:
            end = time.monotonic() + timeout
            url = f"http://127.0.0.1:{port}/api/v1/health"
            while time.monotonic() < end:
                if child.poll() is not None:
                    raise RuntimeError(f"Frozen backend exited with code {child.returncode} before health check")
                try:
                    status, payload = get(url, token)
                    if status == 200:
                        assert b'"status"' in payload
                        break
                except (OSError, TimeoutError):
                    pass
                time.sleep(0.4)
            else:
                raise TimeoutError("Frozen backend health did not become ready")
            assert get(url)[0] == 401, "Unauthenticated health must fail"
            assert get(url, "wrong-token")[0] == 401, "Wrong token must fail"
            assert get(f"http://127.0.0.1:{port}/api/v1/papers")[0] == 401
            assert get(f"http://127.0.0.1:{port}/docs")[0] == 401
            print("Bundled backend smoke PASSED (ready, session auth, isolated user data)")
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    options = parser.parse_args()
    smoke(options.executable)
