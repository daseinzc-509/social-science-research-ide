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
from fastapi.responses import JSONResponse, Response
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


# `max_completion_tokens` is a combined budget for reasoning + final answer.
# Previous 1100 tokens could be entirely consumed by "thinking" on reasoning
# models, leaving message.content=null and finish_reason=length.
# We still allow only ONE potentially billed request per explicit user click.
VISION_MAX_COMPLETION_TOKENS = 4096
VISION_RESPONSE_LIMIT_BYTES = 2_000_000


def _safe_code(value: object) -> str:
    """Only expose provider-defined identifiers, never raw response/prompt text."""
    import re

    if not isinstance(value, (str, int)):
        return ""
    as_text = str(value)
    return as_text if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", as_text) else ""


def _safe_request_id(headers: object) -> str:
    try:
        for header in ("x-request-id", "x-tt-logid", "x-requestid"):
            identifier = _safe_code(headers.get(header, ""))
            if identifier:
                return identifier
    except (TypeError, AttributeError):
        pass
    return ""


def _read_text_content(value: object) -> str:
    """Accept normal Chat Completions text and text-containing content parts."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"text", "output_text"}:
                continue
            text = item.get("text")
            if isinstance(text, dict):  # some providers return {"value": "..."}
                text = text.get("value")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    return ""


def _usage_summary(usage: object) -> str:
    if not isinstance(usage, dict):
        return ""
    bits = []
    output = usage.get("completion_tokens")
    if isinstance(output, int) and 0 <= output < 1_000_000:
        bits.append(f"输出={output}tokens")
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and 0 <= reasoning < 1_000_000:
            bits.append(f"其中思考={reasoning}tokens")
    return "；".join(bits)


def _extract_vision_response(reply: object, *, model: str,
                             request_id: str = "") -> str:
    """Parse provider replies while treating reasoning as *not* a final answer.

    The old implementation swallowed missing/null content, truncated JSON,
    schema differences and model reasoning exhaustion into a misleading
    "does not support image_url" HTTP 502.
    """
    suffix = f"；请求ID={request_id}" if request_id else ""
    model_name = _safe_code(model) or "configured-vision-model"
    label = f"视觉模型 {model_name}"
    if not isinstance(reply, dict):
        raise HTTPException(
            502, f"{label}返回非对象 JSON，无法提取图像说明{suffix}。"
        )

    provider_error = reply.get("error")
    if isinstance(provider_error, dict):
        code = _safe_code(provider_error.get("code")) or _safe_code(
            provider_error.get("type")
        ) or "unknown"
        raise HTTPException(
            502, f"{label}返回服务商错误码 {code}（即使 HTTP 状态为 200）；"
                 f"请检查模型权限及图片输入能力{suffix}。"
        )

    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices:
        # The configured endpoint is expected to return Chat Completions.
        # Some gateways return Responses-like output; accept only explicit
        # final textual content, never `reasoning`/`analysis` strings.
        alternate = _read_text_content(reply.get("output_text"))
        if not alternate and isinstance(reply.get("output"), list):
            alternate = "\n".join(
                _read_text_content(block.get("content"))
                for block in reply["output"]
                if isinstance(block, dict)
                and block.get("type") == "message"
            ).strip()
        if alternate:
            return alternate[:8000]
        raise HTTPException(
            502, f"{label}返回的不是标准 Chat Completions 结果"
                 f"（缺少 choices），检查 Base URL 和 API 接口类型{suffix}。"
        )

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise HTTPException(
            502, f"{label}没有返回 assistant 消息，无法生成图片说明{suffix}。"
        )
    content = _read_text_content(message.get("content"))
    if content:
        return content[:8000]

    reason = _safe_code(first.get("finish_reason"))
    usage = _usage_summary(reply.get("usage"))
    metadata = "；".join(x for x in (
        f"finish_reason={reason}" if reason else "",
        usage,
    ) if x)

    if reason == "length":
        hint = (
            "模型已达到生成上限，可能把输出 tokens 耗在了思考过程，"
            "导致最终 content 为空。当前一次请求的推理＋回答预算"
            f"为 {VISION_MAX_COMPLETION_TOKENS} tokens；"
            "可改用低思考/非推理的视觉模型或进一步调整预算。"
        )
    elif reason == "content_filter":
        hint = "模型输出被内容审核拦截；不应将其视为图片识别成功。"
    elif reason == "tool_calls":
        hint = "模型请求了工具，而当前单图识别不支持工具调用。"
    elif bool(message.get("refusal")):
        hint = "模型拒绝提供图片描述。"
    elif message.get("reasoning_content") or message.get("reasoning"):
        hint = (
            "模型只返回了思考内容，没有可展示的最终回答；"
            "SRA 不会把推理内容伪装成已完成的图像分析。"
        )
    else:
        hint = (
            "模型返回了空的最终回答。请核实 Vision Model ID "
            "确实支持 image_url 输入，以及当前服务是否正确处理图片。"
        )
    additional = f"（{metadata}）" if metadata else ""
    raise HTTPException(502, f"{label}：{hint}{additional}{suffix}")


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
                    "请直接、简洁地用中文识别这张学术图片/统计图中的可辨识标题、"
                    "图例、轴标签、数字和主要关系。不确定处写[不清楚]，不要猜测。"
                    "只输出最终图像描述，不必写详细推理过程。"
                    "说明此结果必须与原 PDF 核对。"
                )},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64,"
                           + base64.b64encode(image_bytes).decode("ascii"),
                    "detail": "high",
                }},
            ]},
        ],
        "stream": False,
        "max_completion_tokens": VISION_MAX_COMPLETION_TOKENS,
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
            request_id = _safe_request_id(getattr(response, "headers", None))
            raw = response.read(VISION_RESPONSE_LIMIT_BYTES + 1)
        if len(raw) > VISION_RESPONSE_LIMIT_BYTES:
            raise HTTPException(
                502, "视觉模型响应过大，超过本地安全上限；"
                     "不会截断后当作不支持图片处理。"
            )
        try:
            reply = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(
                502, "视觉模型未返回完整、可解析的 JSON 响应；"
                     "请检查是否使用 Chat Completions Base URL。"
            ) from None
        return _extract_vision_response(
            reply, model=model, request_id=request_id
        )
    except HTTPError as exc:
        # HTTP errors mean the provider explicitly rejected the request.
        # Existing classifier distinguishes quota vs throttling, never leaks
        # provider's raw message (which might contain private PDF text).
        suffix = _safe_request_id(getattr(exc, "headers", None))
        request_note = f"；请求ID={suffix}" if suffix else ""
        raise HTTPException(
            502, _http_error_message(exc) + request_note
        ) from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(
            502, "视觉模型请求网络失败或超时，可能已经产生费用；"
                 "请先检查服务商记录，不要连续点击重试。"
        ) from None


@figure_router.get("/{figure_id}/preview")
def preview_figure(
    paper_id: str,
    figure_id: str,
    services: ApplicationServices = Depends(get_services),
):
    """Local-only, zero-token preview of exactly what can later be sent.

    The desktop's existing authenticated localhost API is responsible for
    authorizing this endpoint. No external model request is made.
    """
    paper = services.repository.get_paper(paper_id)
    if paper is None:
        raise HTTPException(404, "找不到这篇论文。")
    figure = next(
        (item for item in services.repository.get_figures(paper_id)
         if item.id == figure_id), None
    )
    if figure is None or figure.bbox is None:
        raise HTTPException(404, "该图片没有可核验的 PDF 区域。")
    pdf = services.papers.pdf_path(paper_id)
    _verify_pdf(pdf, paper.sha256)
    image = _render_figure(pdf, figure.page_number, figure.bbox)
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
