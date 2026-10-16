"""Named OpenAI-compatible providers, with separate encrypted per-profile keys.

Existing SRA Lite/Pro/Vision storage remains authoritative for new analysis
jobs. Activating a profile atomically copies its connection into that store;
old in-flight analyses retain their already-created client and settings.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .config import Settings, save_local_environment
from .user_storage import _atomic_bytes, _dpapi_protect, _dpapi_unprotect, _mac_keyring, user_config_dir

_LOCK = threading.RLock()
_SLOTS = {"lite", "pro", "vision"}
_MAC_SERVICE = "org.daseinzc509.sra.model-profiles"
_MAC_KEY = "all-profile-keys"


def _meta_file() -> Path:
    return user_config_dir() / "model-profiles.json"


def _secret_file() -> Path:
    return user_config_dir() / "model-profiles.dpapi"


def read_profile_keys() -> dict[str, str]:
    if sys.platform == "win32":
        path = _secret_file()
        if not path.exists():
            return {}
        raw = _dpapi_unprotect(base64.b64decode(path.read_bytes(), validate=True))
    elif sys.platform == "darwin":
        raw_text = _mac_keyring().get_password(_MAC_SERVICE, _MAC_KEY)
        raw = (raw_text or "{}").encode("utf-8")
    else:
        # Fail closed. Linux may read env API keys, but named profile keys are
        # not persisted until a secure system keyring is explicitly supported.
        return {}
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("Invalid protected SRA profile credentials")
    return value


def write_profile_keys(value: dict[str, str]) -> None:
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("Invalid profile credential data")
    if sys.platform == "win32":
        if not value:
            _secret_file().unlink(missing_ok=True)
            return
        _atomic_bytes(_secret_file(), base64.b64encode(_dpapi_protect(json.dumps(value).encode("utf-8"))))
    elif sys.platform == "darwin":
        store = _mac_keyring()
        if value:
            store.set_password(_MAC_SERVICE, _MAC_KEY, json.dumps(value))
        elif store.get_password(_MAC_SERVICE, _MAC_KEY) is not None:
            store.delete_password(_MAC_SERVICE, _MAC_KEY)
    else:
        raise RuntimeError("Named profile API keys require Windows DPAPI or macOS Keychain")


def _registry() -> dict:
    path = _meta_file()
    if not path.exists():
        return {"profiles": [], "active": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("profiles"), list) or not isinstance(value.get("active"), dict):
        raise ValueError("Invalid SRA model profile catalog")
    return value


def _save_registry(value: dict) -> None:
    _atomic_bytes(_meta_file(), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def validate_base_url(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url or len(url) > 1024 or any(ch.isspace() for ch in url):
        raise ValueError("Invalid API Base URL")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError
        _ = parsed.port
        if url.lower().endswith("/chat/completions"):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Base URL must be an HTTP(S) API root (for example /v1), without credentials or /chat/completions") from exc
    return url


def _clean_profile(payload: dict) -> dict:
    allowed = {"id", "name", "base_url", "model", "max_tokens", "api_key"}
    if set(payload) - allowed:
        raise ValueError("Unsupported profile field")
    name = str(payload.get("name") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not (1 <= len(name) <= 80) or not (1 <= len(model) <= 160):
        raise ValueError("Profile name or model ID is blank or too long")
    if any(x in name + model for x in "\r\n\x00"):
        raise ValueError("Invalid profile name or model ID")
    raw_tokens = payload.get("max_tokens", 8192)
    if type(raw_tokens) is not int or not 1024 <= raw_tokens <= 16000:
        raise ValueError("Max Tokens must be an integer between 1024 and 16000")
    return {"name": name, "model": model, "base_url": validate_base_url(str(payload.get("base_url") or "")), "max_tokens": raw_tokens}


def list_profiles() -> dict:
    with _LOCK:
        state = _registry()
        keys = read_profile_keys()
        return {
            "profiles": [dict(p, has_api_key=bool(keys.get(p["id"]))) for p in state["profiles"]],
            "active": dict(state["active"]),
        }


def save_profile(payload: dict) -> dict:
    clean = _clean_profile(payload)
    requested_id = str(payload.get("id") or "").strip()
    with _LOCK:
        state = _registry()
        existing = next((p for p in state["profiles"] if p["id"] == requested_id), None) if requested_id else None
        if requested_id and existing is None:
            raise ValueError("Unknown profile ID")
        pid = requested_id if existing else uuid4().hex
        api_key = str(payload.get("api_key") or "").strip()
        prior_keys = read_profile_keys()
        if not api_key and not prior_keys.get(pid):
            raise ValueError("Provide an API Key for a new profile")
        if any(ch in api_key for ch in "\n\r") or len(api_key) > 4096:
            raise ValueError("Invalid API Key")
        newer_keys = dict(prior_keys)
        if api_key:
            newer_keys[pid] = api_key
        update = {"id": pid, **clean}
        if existing is None:
            state["profiles"].append(update)
        else:
            state["profiles"] = [update if p["id"] == pid else p for p in state["profiles"]]
        # On failure, don't orphan a changed key without matching metadata.
        write_profile_keys(newer_keys)
        try:
            _save_registry(state)
        except Exception:
            write_profile_keys(prior_keys)
            raise
        return dict(update, has_api_key=True)


def activate_profile(slot: str, profile_id: str) -> dict:
    if slot not in _SLOTS:
        raise ValueError("Unknown AI model slot")
    with _LOCK:
        state = _registry()
        profile = next((p for p in state["profiles"] if p["id"] == profile_id), None)
        if profile is None:
            raise ValueError("Unknown profile ID")
        secret = read_profile_keys().get(profile_id)
        if not secret:
            raise ValueError("Profile has no protected API Key")
        if slot == "lite":
            updates = {"SRA_LITE_API_KEY": secret, "SRA_LITE_API_BASE_URL": profile["base_url"], "SRA_LITE_MODEL": profile["model"]}
        elif slot == "pro":
            updates = {"SRA_PRO_API_KEY": secret, "SRA_PRO_API_BASE_URL": profile["base_url"], "SRA_PRO_MODEL": profile["model"]}
        else:
            updates = {"SRA_VISION_API_KEY": secret, "SRA_VISION_API_BASE_URL": profile["base_url"], "SRA_VISION_MODEL": profile["model"], "SRA_VISION_USE_PRO": "0", "SRA_VISION_ENABLED": "1"}
        # Writes the exact settings consumed by existing real analysis calls.
        # This fails closed on platforms without a secure credential store.
        save_local_environment(updates)
        state["active"][slot] = profile_id
        _save_registry(state)
        return {"slot": slot, "profile_id": profile_id, "model": profile["model"], "max_tokens": profile["max_tokens"]}


def remove_profile(profile_id: str) -> dict:
    with _LOCK:
        state = _registry()
        if profile_id in state["active"].values():
            raise ValueError("Deactivate this profile by activating another one before deleting")
        if not any(p["id"] == profile_id for p in state["profiles"]):
            raise ValueError("Unknown profile ID")
        keys = read_profile_keys()
        state["profiles"] = [p for p in state["profiles"] if p["id"] != profile_id]
        old_state = _registry()
        _save_registry(state)
        keys.pop(profile_id, None)
        try:
            write_profile_keys(keys)
        except Exception:
            _save_registry(old_state)
            raise
        return {"deleted": True}


def active_token_budget(slot: str, model: str) -> int | None:
    """Only take a user's saved budget when slot AND effective model match."""
    state = _registry()
    chosen = state["active"].get(slot)
    if not chosen:
        return None
    profile = next((p for p in state["profiles"] if p["id"] == chosen and p["model"] == model), None)
    if profile is None:
        return None
    settings = Settings.from_environment()
    effective = settings.lite_model if slot == "lite" else settings.pro_model
    if effective != model:
        return None
    configured_base = settings.effective_lite_api_base_url if slot == "lite" else settings.effective_pro_api_base_url
    if (configured_base or "").rstrip("/") != profile["base_url"].rstrip("/"):
        return None
    return int(profile["max_tokens"])


def profile_connection(profile_id: str) -> tuple[str, str, str]:
    with _LOCK:
        profile = next((p for p in _registry()["profiles"] if p["id"] == profile_id), None)
        key = read_profile_keys().get(profile_id)
        if not profile or not key:
            raise ValueError("Unknown or incomplete AI profile")
        return key, profile["base_url"], profile["model"]


def import_existing_connections() -> dict:
    """Copy already-secured stage connections into the named catalog on demand.

    This does not activate them or change any request setting or cache.
    """
    settings = Settings.from_environment()
    stage_items = [
        ("Lite", settings.effective_lite_api_key, settings.effective_lite_api_base_url, settings.lite_model, 4000),
        ("Pro", settings.effective_pro_api_key, settings.effective_pro_api_base_url, settings.pro_model, 8192),
        ("Vision", settings.effective_vision_api_key, settings.effective_vision_api_base_url, settings.vision_model, 8192),
    ]
    imported = []
    for stage, key, base, model, tokens in stage_items:
        if not (key and base and model):
            continue
        existing = _registry()["profiles"]
        stored_keys = read_profile_keys()
        if any(p["model"] == model and p["base_url"] == base
               and stored_keys.get(p["id"]) == key for p in existing):
            continue
        record = save_profile({
            "name": f"现有 {stage} 配置", "base_url": base, "model": model,
            "max_tokens": tokens, "api_key": key,
        })
        imported.append({"id": record["id"], "stage": stage})
    return {"imported": imported, "profiles": list_profiles()}
