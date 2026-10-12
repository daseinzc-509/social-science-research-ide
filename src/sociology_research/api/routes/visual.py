"""Optional PDF formula/vision settings and explicitly requested figure review.

Docling formula recognition runs locally during reparse. Figure analysis sends
only one cropped JPEG to a configured vision-capable Chat Completions API,
never a whole PDF and never automatically during import or Paper Card analysis.
The generated description is AI_INFERRED_UNVERIFIED, not source evidence.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ...config import Settings, save_local_environment
from ...llm_client import _http_error_message
from ...services.application import ApplicationServices
from ..dependencies import get_services

settings_router = APIRouter(prefix="/settings/visual", tags=["settings"])
figure_router = APIRouter(prefix="/papers/{paper_id}/figures", tags=["figures"])

class VisualSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formula_enabled: StrictBool
    vision_enabled: StrictBool
    use_pro_connection: StrictBool
    vision_api_key: str | None = Field(default=None, max_length=4096)
    vision_api_base_url: str | None = Field(default=None, max_length=2048)
    vision_model: str | None = Field(default=None, max_length=150)


class AnalyzeFigureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve_external_send: StrictBool


def _mask(value: str | None) -> str:
    return (value[:4] + "…" + value[-4:]) if value and len(value) > 10 else ("已设置" if value else "")


@settings_router.get("")
def get_visual_settings():
    settings = Settings.from_environment()
    return {
        "formula_enabled": settings.formula_enrichment_enabled,
        "vision_enabled": settings.vision_analysis_enabled,
        "use_pro_connection": settings.uses_pro_vision_connection,
        "has_vision_api_key": bool(settings.vision_api_key),
        "vision_api_key_masked": _mask(settings.vision_api_key),
        "vision_api_base_url": settings.vision_api_base_url or "",
        "vision_model": settings.vision_model or "",
        "note": "Formula uses local Docling, no separate API key; Vision runs on explicit request only.",
    }


@settings_router.put("")
def set_visual_settings(update: VisualSettingsUpdate):
    settings = Settings.from_environment()
    model = (update.vision_model or "").strip()
    url = (update.vision_api_base_url or "").strip()
    key = (update.vision_api_key or "").strip() or settings.vision_api_key or ""
    if update.vision_enabled:
        if not model:
            raise HTTPException(422, "请先填写支持图片输入的 Vision model name。")
        if not update.use_pro_connection and (not key or not url.startswith(("https://", "http://"))):
            raise HTTPException(422, "独立视觉连接需要独立 API Key 和 HTTP(S) Base URL。")
    if model and any(x in model for x in ("\r", "\n")):
        raise HTTPException(422, "Vision model name must be one line.")
    try:
        save_local_environment({
            "SRA_FORMULA_ENRICHMENT": "1" if update.formula_enabled else "0",
            "SRA_VISION_ENABLED": "1" if update.vision_enabled else "0",
            "SRA_VISION_USE_PRO": "1" if update.use_pro_connection else "0",
            "SRA_VISION_API_KEY": key,
            "SRA_VISION_API_BASE_URL": url,
            "SRA_VISION_MODEL": model,
        })
    except (ValueError, RuntimeError) as exc:
        # Never return a provider response or secret in an error.
        raise HTTPException(422, f"无法安全保存视觉设置：{type(exc).__name__}") from None
    return get_visual_settings()


def _render_figure(pdf: Path, page_no: int, bounds: tuple[float, float, float, float]) -> bytes:
    """Read only the true Docling figure bounds in top-left PDF-point coordinates."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise HTTPException(503, "安装包缺少 PDFium 渲染组件。") from None

    try:
        document = pdfium.PdfDocument(str(pdf))
        try:
            if page_no < 1 or page_no > len(document):
                raise HTTPException(422, "无效的图片来源页码。")
            page = document[page_no - 1]
            try:
                width, height = map(float, page.get_size())
                left, top, right, bottom = (float(x) for x in bounds)
                if (left < 0 or top < 0 or right <= left or bottom <= top or
                        right > width + 2 or bottom > height + 2):
                    raise HTTPException(422, "图像区域超出 PDF 页面，请重新解析。")
                scale = min(2.0, 2048 / max(width, height))
                raster = page.render(scale=scale)
                try:
                    image = raster.to_pil().convert("RGB")
                    padding = 6
                    box = (
                        max(0, int((left - padding) * scale)),
                        max(0, int((top - padding) * scale)),
                        min(image.width, int((right + padding) * scale)),
                        min(image.height, int((bottom + padding) * scale)),
                    )
                    image = image.crop(box)
                    if image.width < 12 or image.height < 12:
                        raise HTTPException(422, "图像区域太小，无法分析。")
                    if max(image.size) > 1600:
                        image.thumbnail((1600, 1600))
                    with io.BytesIO() as output:
                        image.save(output, format="JPEG", quality=84, optimize=True)
                        data = output.getvalue()
                    if len(data) > 3_000_000:
                        raise HTTPException(422, "图像区域过大，请缩小复核区域。")
                    return data
                finally:
                    raster.close() if hasattr(raster, "close") else None
            finally:
                page.close() if hasattr(page, "close") else None
        finally:
            document.close() if hasattr(document, "close") else None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "PDF 图像裁剪失败，请检查源文件和区域坐标。") from None


def _verify_pdf(pdf: Path, sha256: str) -> None:
    if not pdf.is_file():
        raise HTTPException(404, "已保存的 PDF 不存在。")
    digest = hashlib.sha256()
    with pdf.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise HTTPException(409, "PDF 内容与导入时不同，已拒绝发送图像。")


def _request_vision(key: str, url: str, model: str, image_bytes: bytes) -> str:
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "You describe only what is visible in a supplied academic figure. "
                "Do not invent values, legends, statistical significance or citations. "
                "Your output is AI_INFERRED_UNVERIFIED, not an observed author claim."
            )},
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "请用中文识别这张学术图片/统计图中的可辨识标题、图例、轴标签、数字和主要关系。"
                    "不确定处写[不清楚]，不要猜测。说明此结果必须与原 PDF 核对。"
                )},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii"),
                    "detail": "high",
                }},
            ]},
        ],
        "stream": False,
        "max_completion_tokens": 1100,
    }
    request = Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            reply = json.loads(response.read(200_000).decode("utf-8"))
        content = reply["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty response")
        return content.strip()[:8000]
    except HTTPError as exc:
        raise HTTPException(502, _http_error_message(exc)) from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, "视觉模型请求网络失败或超时，可能已产生费用；请先检查服务商记录。") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "视觉模型没有返回可用的图像说明，请核对模型是否支持 image_url 输入。") from None


@figure_router.post("/{figure_id}/analyze")
def analyze_figure(
    paper_id: str,
    figure_id: str,
    payload: AnalyzeFigureRequest,
    services: ApplicationServices = Depends(get_services),
):
    if payload.approve_external_send is not True:
        raise HTTPException(422, "需要主动确认将该图片区域发送给视觉模型。")
    settings = Settings.from_environment()
    if not settings.vision_analysis_enabled:
        raise HTTPException(409, "图片增强未在设置中启用。")
    try:
        key, base_url, model = settings.require_vision_configuration()
    except ValueError:
        raise HTTPException(409, "视觉模型配置不完整：请设置 Vision 模型及 API 连接。") from None
    paper = services.repository.get_paper(paper_id)
    if paper is None:
        raise HTTPException(404, "找不到这篇论文。")
    figure = next(
        (f for f in services.repository.get_figures(paper_id) if f.id == figure_id),
        None
    )
    if figure is None or figure.bbox is None:
        raise HTTPException(404, "该图片没有可核验的 PDF 坐标。")
    pdf = services.papers.pdf_path(paper_id)
    _verify_pdf(pdf, paper.sha256)
    cropped = _render_figure(pdf, figure.page_number, figure.bbox)
    content = _request_vision(key, base_url, model, cropped)
    return JSONResponse(
        {
            "figure_id": figure.id,
            "page_number": figure.page_number,
            "model": model,
            "description": content,
            "provenance": "AI_INFERRED_UNVERIFIED",
            "saved_to_card": False,
            "requires_pdf_verification": True,
        },
        headers={"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"},
    )
