"""独立托管 CPU-only Docling 本地解析引擎的生命周期管理。

设计目标：
1. 源码开发与完整打包（full）模式下，若当前 Python 环境已安装 Docling，直接零开销运行；
2. 瘦壳打包（slim）模式下，主程序不含 torch / docling / transformers / scipy / cv2，
   研究者在「设置 → 模型配置中心 → Docling / 增强」点击一次即可在
   `user_root()/engine/docling/venv` 中按需安装独立 CPU-only Docling 运行时；
3. 安装与验证子进程严格复用 `network_settings.download_environment()`（继承用户下载代理，
   剥离全部 AI API Key / Token），并在独立离线子进程中完成真实导入与 PyTorch CPU 自检后，
   才原子写入状态清单。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .network_settings import download_environment
from .user_storage import _atomic_bytes, user_config_dir, user_engine_dir, user_root

PINNED_DOCLING_VERSION = "2.134.0"
PYTORCH_CPU_INDEX_URL = "https://download.pytorch.org/whl/cpu"

# 支持官方源与国内常用镜像；PyTorch CPU 轮子始终优先指向官方 CPU index 避免拉取 CUDA 巨包
MIRROR_PRESETS: dict[str, dict[str, str]] = {
    "default": {
        "label": "官方源（PyPI + PyTorch CPU）",
        "pypi_index": "https://pypi.org/simple",
        "torch_index": PYTORCH_CPU_INDEX_URL,
    },
    "tsinghua": {
        "label": "清华大学 PyPI 镜像（推荐国内）",
        "pypi_index": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "torch_index": PYTORCH_CPU_INDEX_URL,
    },
    "aliyun": {
        "label": "阿里云 PyPI 镜像（推荐国内）",
        "pypi_index": "https://mirrors.aliyun.com/pypi/simple/",
        "torch_index": PYTORCH_CPU_INDEX_URL,
    },
}

TORCH_CPU_PACKAGES: tuple[str, ...] = ("torch", "torchvision")
CORE_ENGINE_PACKAGES: tuple[str, ...] = (
    f"docling=={PINNED_DOCLING_VERSION}",
    "rapidocr",
    "rapidocr-onnxruntime",
    "pypdfium2",
    "pydantic>=2.7,<3",
)

_ENGINE_LOCK = threading.Lock()


def engine_directory() -> Path:
    return user_engine_dir() / "docling"


def engine_venv_dir() -> Path:
    return engine_directory() / "venv"


def engine_python_executable(venv_dir: Path | None = None) -> Path:
    base = venv_dir if venv_dir is not None else engine_venv_dir()
    if sys.platform == "win32":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def engine_state_path() -> Path:
    return user_config_dir() / "docling-engine-state.json"


def worker_package_root() -> Path | None:
    """定位可供托管虚拟环境通过 PYTHONPATH 导入 sociology_research 的目录。"""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = getattr(sys, "_MEIPASS", None)
        candidates = []
        if meipass:
            candidates.append(Path(meipass) / "sra_src")
        candidates.extend([
            exe_dir / "_internal" / "sra_src",
            exe_dir / "sra_src",
        ])
        for candidate in candidates:
            if (candidate / "sociology_research" / "__init__.py").is_file():
                return candidate
        return None
    src_dir = Path(__file__).resolve().parents[1]
    if (src_dir / "sociology_research" / "__init__.py").is_file():
        return src_dir
    return None


def find_uv_executable() -> Path | None:
    """按优先级查找 uv 可执行文件：显式环境变量 → 应用随包目录 → 系统 PATH。"""
    explicit = os.environ.get("SRA_UV_PATH", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate.resolve()

    binary = "uv.exe" if sys.platform == "win32" else "uv"
    exe_dir = Path(sys.executable).resolve().parent
    search_dirs = [exe_dir, exe_dir / "tools", exe_dir / "_internal", exe_dir / "_internal" / "tools"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        search_dirs.extend([mp, mp / "tools"])
    for folder in search_dirs:
        candidate = folder / binary
        if candidate.is_file():
            return candidate.resolve()

    discovered = shutil.which("uv")
    return Path(discovered).resolve() if discovered else None


def embedded_docling_version() -> str | None:
    """检测当前进程自身是否已内置安装 Docling。"""
    try:
        if importlib.util.find_spec("docling") is None:
            return None
    except (ModuleNotFoundError, ValueError):
        return None
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def read_engine_state() -> dict[str, Any]:
    path = engine_state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_engine_state(state: dict[str, Any]) -> None:
    payload = (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_bytes(engine_state_path(), payload)


def managed_engine_ready() -> bool:
    """只有托管虚拟环境 Python 存在且通过独立离线自检后才视为就绪。"""
    state = read_engine_state()
    if state.get("verified") is not True:
        return False
    version = str(state.get("docling_version", "") or "").strip()
    if not version or version == "missing":
        return False
    expected_python = engine_python_executable()
    if not expected_python.is_file():
        return False
    recorded = str(state.get("python_executable", "") or "").strip()
    if recorded:
        try:
            if Path(recorded).resolve() != expected_python.resolve():
                return False
        except OSError:
            return False
    return True


def active_docling_version() -> str:
    """返回当前生效的 Docling 版本（内置优先，其次已验证的独立托管引擎）。"""
    embedded = embedded_docling_version()
    if embedded:
        return embedded
    if managed_engine_ready():
        return str(read_engine_state().get("docling_version", "missing"))
    return "missing"


def validate_mirror_preset(preset: str) -> str:
    key = str(preset or "default").strip().casefold() or "default"
    if key not in MIRROR_PRESETS:
        allowed = ", ".join(MIRROR_PRESETS)
        raise ValueError(f"Unsupported mirror_preset '{preset}'. Allowed: {allowed}")
    return key


def validate_custom_pypi_index(raw: str) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if any(ch.isspace() for ch in url):
        raise ValueError("Custom PyPI index URL must not contain whitespace.")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    is_local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.username or parsed.password:
        raise ValueError("Custom PyPI index URL must not embed credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Custom PyPI index URL must not include query parameters or fragments.")
    if parsed.scheme == "https":
        if not host:
            raise ValueError("Custom PyPI index URL requires a valid hostname.")
    elif parsed.scheme == "http" and is_local:
        pass
    else:
        raise ValueError("Custom PyPI index URL must use https:// (http:// is allowed only for localhost).")
    return url.rstrip("/")


def engine_status() -> dict[str, Any]:
    """返回本地 Docling 解析引擎的完整可读状态（零副作用，不创建目录）。"""
    embedded_ver = embedded_docling_version()
    managed_ok = managed_engine_ready()
    state = read_engine_state()
    uv_path = find_uv_executable()

    if embedded_ver:
        mode = "embedded"
        version = embedded_ver
    elif managed_ok:
        mode = "managed"
        version = str(state.get("docling_version", "missing"))
    else:
        mode = "missing"
        version = "missing"

    return {
        "mode": mode,
        "ready": bool(embedded_ver or managed_ok),
        "embedded_available": bool(embedded_ver),
        "managed_available": bool(managed_ok),
        "docling_version": version,
        "torch_version": str(state.get("torch_version", "")) if managed_ok else "",
        "pinned_docling_version": PINNED_DOCLING_VERSION,
        "engine_dir": str(engine_directory().resolve()),
        "python_executable": str(engine_python_executable().resolve()),
        "uv_available": uv_path is not None,
        "uv_path": str(uv_path) if uv_path is not None else "",
        "mirror_preset": str(state.get("mirror_preset", "default") or "default"),
        "custom_pypi_index": str(state.get("custom_pypi_index", "") or ""),
        "installed_at": str(state.get("installed_at", "")) if managed_ok else "",
        "verified_at": str(state.get("verified_at", "")) if managed_ok else "",
        "mirrors": [
            {"id": key, "label": item["label"]}
            for key, item in MIRROR_PRESETS.items()
        ],
    }


def _emit(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _engine_install_env(*, offline: bool = False) -> dict[str, str]:
    """构造隔离的引擎安装/自检环境：继承下载代理、剥离 API Key、收敛缓存目录。"""
    env = download_environment(offline=offline)
    cache_root = user_root() / "cache"
    env.setdefault("UV_CACHE_DIR", str(cache_root / "uv"))
    env.setdefault("PIP_CACHE_DIR", str(cache_root / "pip"))
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONUTF8"] = "1"
    # 防止开发机激活的外部 venv 干扰 uv/pip 目标环境识别
    env.pop("VIRTUAL_ENV", None)
    return env


def _default_command_runner(
    args: list[str],
    *,
    env: dict[str, str],
    progress: Callable[[str], None] | None,
    stage_label: str,
) -> tuple[int, list[str]]:
    """无 shell 执行子进程，转发精简进度并在取消/异常时终止子进程。"""
    captured: list[str] = []
    with subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as child:
        assert child.stdout is not None
        try:
            for raw_line in child.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                captured.append(line[:240])
                if len(captured) > 40:
                    captured.pop(0)
                if line.startswith("SRA_ENGINE_PROBE_JSON:"):
                    continue
                # 仅转发安全的摘要阶段信息，不外泄含查询参数/认证信息的原始链接
                if "http://" in line or "https://" in line or "@" in line:
                    continue
                if any(
                    token in line
                    for token in (
                        "Resolved ",
                        "Prepared ",
                        "Installed ",
                        "Downloaded ",
                        "Creating virtual environment",
                        "Using CPython",
                        "Collecting ",
                        "Successfully installed",
                    )
                ):
                    _emit(progress, f"{stage_label}: {line[:150]}")
            code = child.wait()
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            raise
    return code, captured


_VERIFY_PROBE_SCRIPT = (
    "import importlib.metadata, json, pathlib, sys; "
    "import torch, pypdfium2, rapidocr; "
    "from docling.document_converter import DocumentConverter; "
    "from docling.datamodel.pipeline_options import PdfPipelineOptions; "
    "manifest = pathlib.Path(rapidocr.__file__).resolve().parent / 'default_models.yaml'; "
    "assert manifest.is_file() and manifest.stat().st_size > 0, 'RapidOCR default_models.yaml missing'; "
    "assert int(torch.tensor([2, 3]).sum().item()) == 5, 'PyTorch CPU tensor check failed'; "
    "payload = {"
    "'docling_version': importlib.metadata.version('docling'), "
    "'torch_version': getattr(torch, '__version__', ''), "
    "'python_version': sys.version.split()[0]"
    "}; "
    "print('SRA_ENGINE_PROBE_JSON:' + json.dumps(payload), flush=True)"
)


def _extract_probe_payload(lines: list[str]) -> dict[str, str]:
    for line in reversed(lines):
        if line.startswith("SRA_ENGINE_PROBE_JSON:"):
            raw = line.split("SRA_ENGINE_PROBE_JSON:", 1)[1].strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
    return {}


def verify_managed_engine(
    *,
    progress: Callable[[str], None] | None = None,
    runner: Callable[..., tuple[int, list[str]]] | None = None,
) -> dict[str, Any]:
    """在独立离线子进程中验证托管 Docling 引擎与 RapidOCR/PyTorch CPU 运行时。"""
    py_exe = engine_python_executable()
    if not py_exe.is_file():
        raise RuntimeError(
            "尚未安装独立托管 Docling 引擎，请先点击「一键安装 / 更新 Docling 引擎（CPU 版）」。"
        )
    run = runner or _default_command_runner
    env = _engine_install_env(offline=True)
    _emit(progress, "正在独立离线进程中验证本地 Docling 引擎（PyTorch CPU / RapidOCR / PDFium）…")
    code, lines = run(
        [str(py_exe), "-c", _VERIFY_PROBE_SCRIPT],
        env=env,
        progress=progress,
        stage_label="引擎自检",
    )
    if code != 0:
        raise RuntimeError(
            f"本地 Docling 引擎自检失败（退出码 {code}）。请重新安装引擎或检查磁盘空间与网络代理。"
        )
    probe = _extract_probe_payload(lines)
    docling_ver = probe.get("docling_version", "").strip()
    if not docling_ver:
        raise RuntimeError("本地 Docling 引擎自检未返回有效的 Docling 版本信息。")

    previous = read_engine_state()
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "verified": True,
        "docling_version": docling_ver,
        "torch_version": probe.get("torch_version", ""),
        "python_version": probe.get("python_version", ""),
        "python_executable": str(py_exe.resolve()),
        "mirror_preset": str(previous.get("mirror_preset", "default") or "default"),
        "custom_pypi_index": str(previous.get("custom_pypi_index", "") or ""),
        "installed_at": str(previous.get("installed_at", "") or now),
        "verified_at": now,
    }
    _write_engine_state(state)
    _emit(
        progress,
        f"本地 Docling 引擎验证通过：docling {docling_ver}（torch {state['torch_version'] or 'cpu'}）。",
    )
    return engine_status()


def install_managed_engine(
    *,
    mirror_preset: str = "default",
    custom_pypi_index: str = "",
    progress: Callable[[str], None] | None = None,
    runner: Callable[..., tuple[int, list[str]]] | None = None,
) -> dict[str, Any]:
    """在 `user_root()/engine/docling/venv` 安装独立 CPU-only Docling 运行时并自检。"""
    preset = validate_mirror_preset(mirror_preset)
    custom_index = validate_custom_pypi_index(custom_pypi_index)
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有本地 Docling 引擎安装或清理任务正在运行，请稍候。")
    try:
        run = runner or _default_command_runner
        env = _engine_install_env(offline=False)
        preset_cfg = MIRROR_PRESETS[preset]
        pypi_index = custom_index or preset_cfg["pypi_index"]
        torch_index = preset_cfg["torch_index"]

        root_dir = engine_directory()
        venv_dir = engine_venv_dir()
        py_exe = engine_python_executable(venv_dir)
        root_dir.mkdir(parents=True, exist_ok=True)
        Path(env["UV_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
        Path(env["PIP_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)

        # 安装开始前先清除旧 verified 标记，防止中途失败留下假就绪状态
        previous = read_engine_state()
        if previous:
            _write_engine_state({**previous, "verified": False})

        uv_exe = find_uv_executable()
        if uv_exe is not None:
            _emit(progress, f"正在使用 uv 创建独立 Python 3.12 解析环境（源：{preset_cfg['label']}）…")
            steps = [
                (
                    "创建虚拟环境",
                    [str(uv_exe), "venv", "--python", "3.12", "--clear", str(venv_dir)],
                ),
                (
                    "安装 PyTorch CPU 运行时",
                    [
                        str(uv_exe),
                        "pip",
                        "install",
                        "--python",
                        str(py_exe),
                        "--index-url",
                        torch_index,
                        *TORCH_CPU_PACKAGES,
                    ],
                ),
                (
                    f"安装 Docling {PINNED_DOCLING_VERSION} 与 RapidOCR",
                    [
                        str(uv_exe),
                        "pip",
                        "install",
                        "--python",
                        str(py_exe),
                        "--index-url",
                        pypi_index,
                        "--extra-index-url",
                        torch_index,
                        *CORE_ENGINE_PACKAGES,
                    ],
                ),
            ]
        elif not getattr(sys, "frozen", False):
            _emit(progress, f"未检测到 uv，改用本地 Python venv + pip 安装（源：{preset_cfg['label']}）…")
            steps = [
                (
                    "创建虚拟环境",
                    [sys.executable, "-m", "venv", "--clear", str(venv_dir)],
                ),
                (
                    "安装 PyTorch CPU 运行时",
                    [
                        str(py_exe),
                        "-m",
                        "pip",
                        "install",
                        "--no-input",
                        "--index-url",
                        torch_index,
                        *TORCH_CPU_PACKAGES,
                    ],
                ),
                (
                    f"安装 Docling {PINNED_DOCLING_VERSION} 与 RapidOCR",
                    [
                        str(py_exe),
                        "-m",
                        "pip",
                        "install",
                        "--no-input",
                        "--index-url",
                        pypi_index,
                        "--extra-index-url",
                        torch_index,
                        *CORE_ENGINE_PACKAGES,
                    ],
                ),
            ]
        else:
            raise RuntimeError(
                "未找到随包 uv 工具，无法在打包版中创建独立 Python 引擎环境。"
                "请重新安装最新版 SRA Desktop，或先安装 uv 后重试。"
            )

        for label, command in steps:
            _emit(progress, f"引擎安装阶段：{label}…")
            code, _lines = run(command, env=env, progress=progress, stage_label=label)
            if code != 0:
                raise RuntimeError(
                    f"本地 Docling 引擎在「{label}」阶段失败（退出码 {code}）。"
                    "请检查「网络代理」设置或切换国内镜像后重试。"
                )

        now = datetime.now(timezone.utc).isoformat()
        _write_engine_state(
            {
                "verified": False,
                "mirror_preset": preset,
                "custom_pypi_index": custom_index,
                "installed_at": now,
                "python_executable": str(py_exe.resolve()),
            }
        )
        return verify_managed_engine(progress=progress, runner=runner)
    finally:
        _ENGINE_LOCK.release()


def uninstall_managed_engine() -> dict[str, Any]:
    """安全删除 SRA 独立托管引擎目录，不影响研究数据、PDF 与内置开发环境。"""
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有本地 Docling 引擎任务正在运行，请稍后再试。")
    try:
        base = user_engine_dir().resolve()
        target = engine_directory().resolve()
        if base not in target.parents:
            raise ValueError("Refusing to remove engine directory outside SRA engine root.")
        if target.is_symlink():
            raise ValueError("Refusing to remove symlinked engine directory.")
        if target.exists():
            shutil.rmtree(target)
        engine_state_path().unlink(missing_ok=True)
        return engine_status()
    finally:
        _ENGINE_LOCK.release()
