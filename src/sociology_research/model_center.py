"""SRA model asset and network management core.

This module intentionally does not own PDF evidence, Paper Cards or model analysis.
All API secrets remain in the platform credential manager; no plaintext fallback.
"""
from __future__ import annotations

import contextlib
import inspect
import importlib.metadata
import json
import os
import re
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit
from urllib.request import getproxies
from uuid import uuid4

_RUNTIME_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "HF_HUB_OFFLINE", "DOCLING_ARTIFACTS_PATH", "HF_HOME",
)
_LOOPBACK = "localhost,127.0.0.1,::1"
_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 ._\-]{0,79}$")
_LOCK = threading.RLock()


def _root() -> Path:
    override = os.environ.get("SRA_MODEL_CENTER_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData/Local") / "SRA"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/SRA"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share") / "SRA"


def validate_proxy(value: str) -> str:
    value = (value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError("代理地址必须是 http(s)://host:port 或 socks5(h)://host:port")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("代理 URL 不能包含用户密码、路径、查询或锚点")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("代理端口不合法") from exc
    if not port or not 1 <= port <= 65535:
        raise ValueError("代理地址需要有效的端口")
    return value.rstrip("/")


@dataclass(frozen=True)
class NetworkConfig:
    mode: str = "system"  # direct | system | manual
    proxy_url: str = ""
    formula_models: bool = False
    use_managed_models: bool = False

    def validated(self) -> "NetworkConfig":
        if self.mode not in {"direct", "system", "manual"}:
            raise ValueError("网络模式只能是 direct/system/manual")
        url = validate_proxy(self.proxy_url) if self.mode == "manual" else ""
        return NetworkConfig(mode=self.mode, proxy_url=url, formula_models=bool(self.formula_models), use_managed_models=bool(self.use_managed_models))


def _config_path() -> Path:
    return _root() / "model-center-config.json"


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("配置文件格式损坏")
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_config() -> NetworkConfig:
    raw = _read_json(_config_path(), {})
    return NetworkConfig(
        mode=str(raw.get("mode", "system")),
        proxy_url=str(raw.get("proxy_url", "")),
        formula_models=bool(raw.get("formula_models", False)),
        use_managed_models=bool(raw.get("use_managed_models", False)),
    ).validated()


def save_config(config: NetworkConfig) -> NetworkConfig:
    result = config.validated()
    with _LOCK:
        old = read_config()
        _atomic_write(_config_path(), asdict(result))
        if result.formula_models != old.formula_models:
            _marker().unlink(missing_ok=True)
    return result


def artifacts_path() -> Path:
    return _root() / "docling-models"


def _env_from_network(config: NetworkConfig, *, offline: bool) -> dict[str, str | None]:
    config = config.validated()
    updates: dict[str, str | None] = {key: None for key in _RUNTIME_ENV_KEYS}
    updates["DOCLING_ARTIFACTS_PATH"] = str(artifacts_path())
    updates["HF_HOME"] = str(_root() / "huggingface-cache")
    updates["HF_HUB_OFFLINE"] = "1" if offline else None
    if config.mode == "system":
        # On Windows getproxies() reads the OS proxy registry when environment
        # variables are absent. HTTP libraries generally do NOT do this on their
        # own, so make the detected proxy explicit for the Docling downloader.
        detected = getproxies()
        for name in ("http", "https", "all"):
            proxy = os.environ.get(name.upper() + "_PROXY") or os.environ.get(name + "_proxy") or detected.get(name)
            if proxy:
                # WinINET can return host:port without a URL scheme.
                proxy = proxy if "://" in proxy else "http://" + proxy
                updates[name.upper() + "_PROXY"] = proxy
                updates[name + "_proxy"] = proxy
    elif config.mode == "manual":
        updates["ALL_PROXY"] = config.proxy_url
        updates["all_proxy"] = config.proxy_url
        updates["HTTPS_PROXY"] = config.proxy_url
        updates["HTTP_PROXY"] = config.proxy_url
        updates["https_proxy"] = config.proxy_url
        updates["http_proxy"] = config.proxy_url
    no_proxy = os.environ.get("NO_PROXY", "") if config.mode == "system" else ""
    bypass = ",".join(filter(None, (no_proxy, _LOOPBACK)))
    updates["NO_PROXY"] = bypass
    updates["no_proxy"] = bypass
    return updates


@contextlib.contextmanager
def scoped_network(config: NetworkConfig, *, offline: bool = False) -> Iterator[None]:
    """Set downloader environment temporarily; never persist proxy credentials.

    A lock avoids overlapping scoped downloads; caller must keep normal PDF imports
    out of this phase (the REST integration does so with a preflight gate).
    """
    with _LOCK:
        saved = {name: os.environ.get(name) for name in _RUNTIME_ENV_KEYS}
        for key, val in _env_from_network(config, offline=offline).items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        try:
            yield
        finally:
            for key, val in saved.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


def _marker() -> Path:
    return _root() / "model-center-verified.json"


def _docling_version() -> str:
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def model_status() -> dict[str, Any]:
    path = artifacts_path()
    marker = _read_json(_marker(), {})
    has_files = path.is_dir() and any(path.iterdir())
    is_verified = bool(marker.get("verified_offline")) and has_files \
        and marker.get("docling_version") == _docling_version() \
        and bool(marker.get("formula_models")) == read_config().formula_models
    return {
        "model_dir": str(path),
        "has_files": has_files,
        "verified_offline": is_verified,
        "verified_at": marker.get("verified_at"),
        "formula_models": bool(marker.get("formula_models")),
        "state": "verified" if is_verified else ("needs_verification" if has_files else "not_downloaded"),
    }


def download_models(*, downloader: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Call the installed Docling's own model downloader, not a guessed HF URL.

    Completeness is *not* claimed until verify_offline has run successfully.
    """
    conf = read_config()
    if not conf.use_managed_models:
        save_config(NetworkConfig(mode=conf.mode, proxy_url=conf.proxy_url, formula_models=conf.formula_models, use_managed_models=True))
        conf = read_config()
    path = artifacts_path()
    path.mkdir(parents=True, exist_ok=True)
    _marker().unlink(missing_ok=True)
    if downloader is None:
        from docling.utils.model_downloader import download_models as downloader
    desired = {
        "output_dir": path,
        "force": False,
        "progress": False,
        "with_layout": True,
        "with_tableformer": True,
        "with_rapidocr": True,
        "with_code_formula": conf.formula_models,
        "with_picture_classifier": False,
        "with_easyocr": False,
    }
    sig = inspect.signature(downloader)
    for required in ("output_dir", "with_layout", "with_tableformer", "with_rapidocr"):
        if required not in sig.parameters:
            raise RuntimeError(f"已安装的 Docling 下载接口缺少参数：{required}")
    if conf.formula_models and "with_code_formula" not in sig.parameters:
        raise RuntimeError("此版本 Docling 不支持预下载公式模型")
    args = {k: v for k, v in desired.items() if k in sig.parameters}
    with scoped_network(conf):
        downloader(**args)
    return model_status()


def verify_offline(checker: Callable[[Path], Any]) -> dict[str, Any]:
    """Mark a model installation usable only after a real OFFLINE Docling check.

    The UI/backend should pass a checker that converts a local test PDF through
    the *same* Docling/OCR pipeline used by SRA. Failing checks never write success.
    """
    path = artifacts_path()
    if not path.is_dir() or not any(path.iterdir()):
        raise RuntimeError("模型目录为空，不能标记为离线可用")
    _marker().unlink(missing_ok=True)
    conf = read_config()
    with scoped_network(conf, offline=True):
        checker(path)
    _atomic_write(_marker(), {
        "verified_offline": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "formula_models": conf.formula_models,
        "docling_version": _docling_version(),
    })
    apply_verified_artifacts_at_startup()
    return model_status()


def apply_verified_artifacts_at_startup() -> bool:
    """Configure the local parser without downloading anything at application startup."""
    if not read_config().use_managed_models:
        return False
    status = model_status()
    if not status["verified_offline"]:
        return False
    os.environ["DOCLING_ARTIFACTS_PATH"] = str(artifacts_path())
    os.environ.setdefault("HF_HOME", str(_root() / "huggingface-cache"))
    # Do not globally set HF_HUB_OFFLINE: this also affects unrelated imports/tasks.
    return True


def verified_artifacts_for_parser() -> Path | None:
    """Return pinned local model path, or reject an opted-in incomplete installation."""
    conf = read_config()
    if not conf.use_managed_models:
        return None
    state = model_status()
    if not state["verified_offline"]:
        raise RuntimeError("Docling 本地模型尚未验证：请打开模型中心，下载并运行离线验证")
    return artifacts_path()


def prepare_runtime() -> None:
    """Called before SRA creates analysis services; keeps legacy settings intact."""
    try:
        apply_verified_artifacts_at_startup()
    except (OSError, ValueError):
        # A broken user config should be diagnosable from the settings UI,
        # not prevent all existing SRA features from starting.
        pass
    # The existing SRA settings API persists role configuration in its original
    # protected store. Restore chosen profiles through the same entry point.
    try:
        from .config import save_local_environment
        ProviderProfiles().restore_active(writer=save_local_environment)
    except (ImportError, OSError, ValueError):
        # Fresh installs without keyring/backend settings remain usable.
        pass


def validate_profile(name: str, base_url: str, model: str, role: str) -> dict[str, str]:
    name, model = name.strip(), model.strip()
    if not _RE.fullmatch(name) or not model or len(model) > 160:
        raise ValueError("配置名称或模型 ID 不合法")
    base_url = base_url.strip().rstrip("/")
    url = urlsplit(base_url)
    if url.scheme not in {"https", "http"} or not url.netloc or url.username or url.password or url.fragment:
        raise ValueError("Base URL 必须是有效的 HTTP(S) 地址，且不能包含用户名密码")
    if url.scheme == "http" and (url.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程模型服务必须使用 HTTPS，避免 API Key 通过明文 HTTP 传输")
    if role not in {"lite", "pro", "vision"}:
        raise ValueError("模型用途必须是 lite/pro/vision")
    return {"name": name, "base_url": base_url, "model": model, "role": role}


class ProviderProfiles:
    """Multiple profiles; secrets always stored via OS credential manager."""
    SERVICE = "SRA Model Profiles"

    def __init__(self, keychain=None):
        if keychain is None:
            import keyring as keychain
        self.keychain = keychain
        self.file = _root() / "model-center-profiles.json"

    def list(self) -> list[dict[str, Any]]:
        profiles = _read_json(self.file, {"profiles": []}).get("profiles", [])
        if not isinstance(profiles, list):
            raise ValueError("模型配置文件损坏")
        return [dict(item) for item in profiles]

    def active_ids(self) -> dict[str, str]:
        saved = _read_json(self.file, {"profiles": []}).get("active", {})
        return dict(saved) if isinstance(saved, dict) else {}

    def save(self, *, name: str, base_url: str, model: str, role: str, key: str, profile_id: str | None = None) -> dict[str, Any]:
        checked = validate_profile(name, base_url, model, role)
        if key and ("\n" in key or "\r" in key):
            raise ValueError("API Key 不能包含换行")
        with _LOCK:
            current = self.list()
            if profile_id is None:
                profile_id = uuid4().hex
                if not key.strip():
                    raise ValueError("新增配置必须提供 API Key")
            elif not any(item.get("id") == profile_id for item in current):
                raise KeyError("配置不存在")
            if key.strip():
                # If the secure credential store fails, we do not write a profile.
                self.keychain.set_password(self.SERVICE, profile_id, key.strip())
            entry = dict(checked, id=profile_id)
            new_items = [x for x in current if x.get("id") != profile_id] + [entry]
            _atomic_write(self.file, {"profiles": new_items, "active": self.active_ids()})
        return entry

    def activate(self, profile_id: str, *, writer: Callable[[dict[str, str | None]], Any]) -> dict[str, Any]:
        entry = next((item for item in self.list() if item.get("id") == profile_id), None)
        if entry is None:
            raise KeyError("配置不存在")
        secret = self.keychain.get_password(self.SERVICE, profile_id)
        if not secret:
            raise ValueError("此配置的系统密钥缺失，请重新输入 API Key")
        role = entry["role"].upper()
        updates: dict[str, str | None] = {
            f"SRA_{role}_API_KEY": secret,
            f"SRA_{role}_API_BASE_URL": entry["base_url"],
            f"SRA_{role}_MODEL": entry["model"],
        }
        if role == "VISION":
            updates["SRA_VISION_USE_PRO"] = "0"
            updates["SRA_VISION_ENABLED"] = "1"
        writer(updates)
        # Mark active only after the original secure settings writer succeeds.
        with _LOCK:
            active = self.active_ids()
            active[entry["role"]] = profile_id
            _atomic_write(self.file, {"profiles": self.list(), "active": active})
        return entry

    def restore_active(self, *, writer: Callable[[dict[str, str | None]], Any]) -> None:
        for role, profile_id in self.active_ids().items():
            try:
                entry = next((item for item in self.list() if item.get("id") == profile_id and item.get("role") == role), None)
                if entry is None:
                    continue
                secret = self.keychain.get_password(self.SERVICE, profile_id)
                if secret:
                    updates = {
                        f"SRA_{role.upper()}_API_KEY": secret,
                        f"SRA_{role.upper()}_API_BASE_URL": entry["base_url"],
                        f"SRA_{role.upper()}_MODEL": entry["model"],
                    }
                    writer(updates)
            except Exception:
                # An invalid or missing keychain must not keep the local API from starting.
                continue

    def delete(self, profile_id: str) -> None:
        with _LOCK:
            current = self.list()
            if not any(p.get("id") == profile_id for p in current):
                raise KeyError("配置不存在")
            # Deletion should fail rather than silently leave credentials behind.
            self.keychain.delete_password(self.SERVICE, profile_id)
            active = {r: i for r, i in self.active_ids().items() if i != profile_id}
            _atomic_write(self.file, {"profiles": [p for p in current if p.get("id") != profile_id], "active": active})


def _make_smoke_pdf(path: Path) -> None:
    """Generate a tiny valid PDF without PyMuPDF or ReportLab dependency."""
    message = b"BT /F1 22 Tf 72 700 Td (SRA OFFLINE TEST) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(message)).encode() + b" >>\nstream\n" + message + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    start_xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n".encode())
    path.write_bytes(output)


def docling_smoke_checker(model_dir: Path) -> None:
    """Proof of real local Docling layout/table conversion while HF offline.

    A successful pass does not prove accuracy on real scanned Chinese PDFs.
    """
    import tempfile
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(artifacts_path=model_dir)
    opts.do_ocr = True
    opts.do_table_structure = True
    # Force the same intended OCR engine that the manager prefetches.
    from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions
    # Force OCR on text-bearing PDFs too: otherwise a successful digital-text
    # smoke test can completely miss missing RapidOCR weights or configuration.
    opts.ocr_options = RapidOcrOptions(mode=OcrMode.FULL_PAGE)
    if read_config().formula_models:
        opts.do_formula_enrichment = True
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    with tempfile.TemporaryDirectory(prefix="sra-model-check-") as tmp:
        pdf = Path(tmp) / "offline.pdf"
        _make_smoke_pdf(pdf)
        converted = converter.convert(pdf)
        status = str(getattr(converted, "status", "")).casefold()
        if "success" not in status:
            raise RuntimeError(f"Docling 离线检查失败，状态：{status}")
        document = converted.document
        try:
            text = document.export_to_text()
        except AttributeError:
            text = "\n".join(str(getattr(item[0] if isinstance(item, tuple) else item, "text", ""))
                             for item in document.iterate_items())
        if "SRA OFFLINE TEST" not in text.upper().replace("\n", " "):
            raise RuntimeError("Docling 转换成功但未识别出离线检查文本")


def test_profile_connection(profile_id: str, *, repo: ProviderProfiles | None = None,
                            client: Any | None = None) -> dict[str, Any]:
    """One explicitly requested minimal chat call; never retry or return a secret.

    Note that a 200 only proves this small request works, not a full-paper Pro call.
    """
    import httpx

    repo = repo or ProviderProfiles()
    entry = next((x for x in repo.list() if x.get("id") == profile_id), None)
    if entry is None:
        raise KeyError("配置不存在")
    secret = repo.keychain.get_password(repo.SERVICE, profile_id)
    if not secret:
        raise ValueError("模型 API Key 尚未设置")
    url = entry["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    created_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0, trust_env=True)
    try:
        try:
            response = client.post(
                url,
                headers={"Authorization": "Bearer " + secret},
                json={"model": entry["model"], "messages": [{"role": "user", "content": "Reply OK"}],
                      "max_tokens": 32, "stream": False},
            )
        except httpx.TimeoutException:
            return {"ok": False, "category": "timeout"}
        except httpx.HTTPError:
            return {"ok": False, "category": "network_error"}
        code = response.status_code
        category = (
            "ok" if 200 <= code < 300 else
            "key_invalid" if code == 401 else
            "permission_denied" if code == 403 else
            "endpoint_or_model_not_found" if code == 404 else
            "quota_or_rate_limited" if code == 429 else
            "request_rejected" if 400 <= code < 500 else
            "provider_error" if code >= 500 else "unexpected_status"
        )
        # A proxy landing page can answer HTTP 200 without returning any model result.
        if 200 <= code < 300:
            try:
                body = response.json()
                if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
                    category = "invalid_chat_response"
            except (ValueError, TypeError):
                category = "invalid_chat_response"
        return {"ok": category == "ok", "http_status": code, "category": category,
                "scope": "small_chat_only", "auto_retry": False}
    finally:
        if created_client:
            client.close()
