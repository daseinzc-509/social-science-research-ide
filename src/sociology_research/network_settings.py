"""Separate network policy for model downloads and paid AI inference.

Download policy is applied only to a child process. AI proxy selection is scoped
per HTTP request (no process-wide os.environ mutations). Plaintext preferences
contain no credentials; proxy URLs with embedded credentials are rejected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .user_storage import _atomic_bytes, user_config_dir

_ALLOWED = frozenset({"system", "direct", "custom"})
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def validate_proxy_url(raw: str) -> str:
    url = str(raw or "").strip()
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or any(c.isspace() for c in url)):
            raise ValueError
        port = parsed.port
        if port is None or not 1 <= port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Proxy URL must be http(s)://host:port, with no credentials or path. Use the HTTP proxy port, not SOCKS5.") from exc
    return url.rstrip("/")


def validate_network_settings(payload: dict) -> dict[str, str]:
    if set(payload) - {"download_mode", "download_proxy", "ai_mode", "ai_proxy"}:
        raise ValueError("Unknown network settings")
    result: dict[str, str] = {}
    for prefix in ("download", "ai"):
        mode = str(payload.get(prefix + "_mode", "system") or "system")
        if mode not in _ALLOWED:
            raise ValueError(f"Unsupported {prefix} proxy mode")
        url = str(payload.get(prefix + "_proxy", "") or "").strip()
        if mode == "custom":
            url = validate_proxy_url(url)
        elif url:
            # Preserve an inactive URL only if it is safe and well-formed.
            url = validate_proxy_url(url)
        result[prefix + "_mode"] = mode
        result[prefix + "_proxy"] = url
    return result


def _path() -> Path:
    return user_config_dir() / "network-settings.json"


def read_network_settings() -> dict[str, str]:
    path = _path()
    if not path.is_file():
        return validate_network_settings({})
    return validate_network_settings(json.loads(path.read_text(encoding="utf-8")))


def save_network_settings(data: dict) -> dict[str, str]:
    previous = read_network_settings()
    config = validate_network_settings({**previous, **data})
    _atomic_bytes(_path(), (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return config


def ai_request_opener():
    """Return None to use the legacy urlopen/system-proxy path."""
    config = read_network_settings()
    if config["ai_mode"] == "system":
        return None
    if config["ai_mode"] == "direct":
        return build_opener(ProxyHandler({}))
    url = config["ai_proxy"]
    return build_opener(ProxyHandler({"https": url, "http": url}))


def download_environment(base: dict[str, str] | None = None, *, offline: bool = False) -> dict[str, str]:
    """Create a fresh, isolated worker environment; never modify os.environ."""
    env = dict(os.environ if base is None else base)
    config = read_network_settings()
    if config["download_mode"] != "system":
        for name in _PROXY_KEYS:
            env.pop(name, None)
        if config["download_mode"] == "custom":
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                env[name] = config["download_proxy"]
            # Don't let a broad inherited NO_PROXY bypass the user's choice.
            env["NO_PROXY"] = "localhost,127.0.0.1,::1"
            env["no_proxy"] = env["NO_PROXY"]
        else:
            # Requests on Windows can discover WinINET proxies even without
            # *_PROXY env vars; NO_PROXY=* explicitly forces direct transport.
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
    # No AI credentials are needed to fetch public Docling weights.
    for name in list(env):
        if name.startswith("SRA_") and ("KEY" in name or "TOKEN" in name):
            env.pop(name, None)
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    else:
        env.pop("HF_HUB_OFFLINE", None)
        env.pop("TRANSFORMERS_OFFLINE", None)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env
