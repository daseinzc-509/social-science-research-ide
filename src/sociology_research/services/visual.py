"""公式增强、单图本地预览与按需视觉分析应用服务。

将视觉配置读写、PDF 完整性校验、单图裁剪、响应解析及多模态请求下沉至服务层，
并统一复用 OpenAICompatibleClient 的代理策略与安全错误分类。
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings, save_local_environment
from ..llm_client import (
    ModelRequestError,
    OpenAICompatibleClient,
    _safe_provider_id,
    _safe_request_id,
)
from ..repository import ResearchRepository
from .papers import PaperService

# max_completion_tokens 是思考 + 最终回答的总预算，保持单次点击仅发一次付费请求
VISION_MAX_COMPLETION_TOKENS = 4096
VISION_RESPONSE_LIMIT_BYTES = 2_000_000

VISION_SYSTEM_PROMPT = (
    "You describe only what is visible in a supplied academic figure. "
    "Do not invent values, legends, statistical significance or citations. "
    "Your output is AI_INFERRED_UNVERIFIED, not an observed author claim."
)
VISION_USER_PROMPT = (
    "请直接、简洁地用中文识别这张学术图片/统计图中的可辨识标题、"
    "图例、轴标签、数字和主要关系。不确定处写[不清楚]，不要猜测。"
    "只输出最终图像描述，不必写详细推理过程。"
    "说明此结果必须与原 PDF 核对。"
)


class VisualServiceError(Exception):
    """视觉服务领域异常，携带明确的 HTTP 状态码与安全提示文案。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def mask_secret(value: str | None) -> str:
    return (value[:4] + "…" + value[-4:]) if value and len(value) > 10 else ("已设置" if value else "")


def read_text_content(value: object) -> str:
    """兼容标准 Chat Completions 文本字符串与多段文本块数组。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"text", "output_text"}:
                continue
            text = item.get("text")
            if isinstance(text, dict):  # 兼容部分网关返回 {"value": "..."}
                text = text.get("value")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    return ""


def usage_summary(usage: object) -> str:
    if not isinstance(usage, dict):
        return ""
    bits: list[str] = []
    output = usage.get("completion_tokens")
    if isinstance(output, int) and 0 <= output < 1_000_000:
        bits.append(f"输出={output}tokens")
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = details.get("reasoning_tokens")
        if isinstance(reasoning, int) and 0 <= reasoning < 1_000_000:
            bits.append(f"其中思考={reasoning}tokens")
    return "；".join(bits)


def extract_vision_response(reply: object, *, model: str, request_id: str = "") -> str:
    """解析视觉模型返回，严格区分推理思考文本与最终回答。"""
    suffix = f"；请求ID={request_id}" if request_id else ""
    model_name = _safe_provider_id(model) or "configured-vision-model"
    label = f"视觉模型 {model_name}"
    if not isinstance(reply, dict):
        raise VisualServiceError(502, f"{label}返回非对象 JSON，无法提取图像说明{suffix}。")

    provider_error = reply.get("error")
    if isinstance(provider_error, dict):
        code = _safe_provider_id(provider_error.get("code")) or _safe_provider_id(
            provider_error.get("type")
        ) or "unknown"
        raise VisualServiceError(
            502,
            f"{label}返回服务商错误码 {code}（即使 HTTP 状态为 200）；"
            f"请检查模型权限及图片输入能力{suffix}。",
        )

    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices:
        alternate = read_text_content(reply.get("output_text"))
        if not alternate and isinstance(reply.get("output"), list):
            alternate = "\n".join(
                read_text_content(block.get("content"))
                for block in reply["output"]
                if isinstance(block, dict) and block.get("type") == "message"
            ).strip()
        if alternate:
            return alternate[:8000]
        raise VisualServiceError(
            502,
            f"{label}返回的不是标准 Chat Completions 结果"
            f"（缺少 choices），检查 Base URL 和 API 接口类型{suffix}。",
        )

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise VisualServiceError(502, f"{label}没有返回 assistant 消息，无法生成图片说明{suffix}。")
    content = read_text_content(message.get("content"))
    if content:
        return content[:8000]

    reason = _safe_provider_id(first.get("finish_reason"))
    usage = usage_summary(reply.get("usage"))
    metadata = "；".join(x for x in (f"finish_reason={reason}" if reason else "", usage) if x)

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
    raise VisualServiceError(502, f"{label}：{hint}{additional}{suffix}")


def verify_pdf_integrity(pdf: Path, sha256: str) -> None:
    if not pdf.is_file():
        raise VisualServiceError(404, "已保存的 PDF 不存在。")
    digest = hashlib.sha256()
    with pdf.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise VisualServiceError(409, "PDF 内容与导入时不同，已拒绝发送图像。")


def render_figure_crop(pdf: Path, page_no: int, bounds: tuple[float, float, float, float]) -> bytes:
    """按 Docling 记录的页面坐标安全裁剪单张图片为有界 JPEG。"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise VisualServiceError(503, "安装包缺少 PDFium 渲染组件。") from None

    try:
        document = pdfium.PdfDocument(str(pdf))
        try:
            if page_no < 1 or page_no > len(document):
                raise VisualServiceError(422, "无效的图片来源页码。")
            page = document[page_no - 1]
            try:
                width, height = map(float, page.get_size())
                left, top, right, bottom = (float(x) for x in bounds)
                if (
                    left < 0 or top < 0 or right <= left or bottom <= top
                    or right > width + 2 or bottom > height + 2
                ):
                    raise VisualServiceError(422, "图像区域超出 PDF 页面，请重新解析。")
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
                        raise VisualServiceError(422, "图像区域太小，无法分析。")
                    if max(image.size) > 1600:
                        image.thumbnail((1600, 1600))
                    with io.BytesIO() as output:
                        image.save(output, format="JPEG", quality=84, optimize=True)
                        data = output.getvalue()
                    if len(data) > 3_000_000:
                        raise VisualServiceError(422, "图像区域过大，请缩小复核区域。")
                    return data
                finally:
                    raster.close() if hasattr(raster, "close") else None
            finally:
                page.close() if hasattr(page, "close") else None
        finally:
            document.close() if hasattr(document, "close") else None
    except VisualServiceError:
        raise
    except Exception:
        raise VisualServiceError(422, "PDF 图像裁剪失败，请检查源文件和区域坐标。") from None


def request_vision_description(
    key: str,
    url: str,
    model: str,
    image_bytes: bytes,
    *,
    sender: Callable[..., Any] | None = None,
) -> str:
    """经由统一 OpenAICompatibleClient 执行单次多模态视觉分析请求。"""
    client = OpenAICompatibleClient(api_key=key, base_url=url, timeout_seconds=180)
    try:
        reply, request_id = client.complete_vision(
            model=model,
            system_prompt=VISION_SYSTEM_PROMPT,
            user_prompt=VISION_USER_PROMPT,
            image_bytes=image_bytes,
            max_tokens=VISION_MAX_COMPLETION_TOKENS,
            response_limit_bytes=VISION_RESPONSE_LIMIT_BYTES,
            sender=sender,
        )
    except ModelRequestError as exc:
        raise VisualServiceError(502, str(exc)) from None
    return extract_vision_response(reply, model=model, request_id=request_id)


class VisualService:
    """图表预览、视觉配置与按需视觉分析应用服务。"""

    def __init__(self, repository: ResearchRepository, papers: PaperService) -> None:
        self.repository = repository
        self.papers = papers

    def get_settings(self, *, settings_loader: Callable[[], Settings] = Settings.from_environment) -> dict[str, Any]:
        settings = settings_loader()
        return {
            "formula_enabled": settings.formula_enrichment_enabled,
            "vision_enabled": settings.vision_analysis_enabled,
            "use_pro_connection": settings.uses_pro_vision_connection,
            "has_vision_api_key": bool(settings.vision_api_key),
            "vision_api_key_masked": mask_secret(settings.vision_api_key),
            "vision_api_base_url": settings.vision_api_base_url or "",
            "vision_model": settings.vision_model or "",
            "note": "Formula uses local Docling, no separate API key; Vision runs on explicit request only.",
        }

    def update_settings(
        self,
        *,
        formula_enabled: bool,
        vision_enabled: bool,
        use_pro_connection: bool,
        vision_api_key: str | None,
        vision_api_base_url: str | None,
        vision_model: str | None,
        settings_loader: Callable[[], Settings] = Settings.from_environment,
        env_saver: Callable[[dict[str, str | None]], Any] = save_local_environment,
    ) -> dict[str, Any]:
        settings = settings_loader()
        model = (vision_model or "").strip()
        url = (vision_api_base_url or "").strip()
        key = (vision_api_key or "").strip() or settings.vision_api_key or ""
        if vision_enabled:
            if not model:
                raise VisualServiceError(422, "请先填写支持图片输入的 Vision model name。")
            if not use_pro_connection and (not key or not url.startswith(("https://", "http://"))):
                raise VisualServiceError(422, "独立视觉连接需要独立 API Key 和 HTTP(S) Base URL。")
        if model and any(x in model for x in ("\r", "\n")):
            raise VisualServiceError(422, "Vision model name must be one line.")
        try:
            env_saver({
                "SRA_FORMULA_ENRICHMENT": "1" if formula_enabled else "0",
                "SRA_VISION_ENABLED": "1" if vision_enabled else "0",
                "SRA_VISION_USE_PRO": "1" if use_pro_connection else "0",
                "SRA_VISION_API_KEY": key,
                "SRA_VISION_API_BASE_URL": url,
                "SRA_VISION_MODEL": model,
            })
        except (ValueError, RuntimeError) as exc:
            raise VisualServiceError(422, f"无法安全保存视觉设置：{type(exc).__name__}") from None
        return self.get_settings(settings_loader=settings_loader)

    def preview_figure(
        self,
        paper_id: str,
        figure_id: str,
        *,
        renderer: Callable[[Path, int, tuple[float, float, float, float]], bytes] = render_figure_crop,
    ) -> bytes:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise VisualServiceError(404, "找不到这篇论文。")
        figure = next(
            (item for item in self.repository.get_figures(paper_id) if item.id == figure_id),
            None,
        )
        if figure is None or figure.bbox is None:
            raise VisualServiceError(404, "该图片没有可核验的 PDF 区域。")
        pdf = self.papers.pdf_path(paper_id)
        verify_pdf_integrity(pdf, paper.sha256)
        return renderer(pdf, figure.page_number, figure.bbox)

    def analyze_figure(
        self,
        paper_id: str,
        figure_id: str,
        *,
        approve_external_send: bool,
        settings_loader: Callable[[], Settings] = Settings.from_environment,
        pdf_verifier: Callable[[Path, str], None] = verify_pdf_integrity,
        renderer: Callable[[Path, int, tuple[float, float, float, float]], bytes] = render_figure_crop,
        vision_requester: Callable[[str, str, str, bytes], str] = request_vision_description,
    ) -> dict[str, Any]:
        if approve_external_send is not True:
            raise VisualServiceError(422, "需要主动确认将该图片区域发送给视觉模型。")
        settings = settings_loader()
        if not settings.vision_analysis_enabled:
            raise VisualServiceError(409, "图片增强未在设置中启用。")
        try:
            key, base_url, model = settings.require_vision_configuration()
        except ValueError:
            raise VisualServiceError(409, "视觉模型配置不完整：请设置 Vision 模型及 API 连接。") from None
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise VisualServiceError(404, "找不到这篇论文。")
        figure = next(
            (f for f in self.repository.get_figures(paper_id) if f.id == figure_id),
            None,
        )
        if figure is None or figure.bbox is None:
            raise VisualServiceError(404, "该图片没有可核验的 PDF 坐标。")
        pdf = self.papers.pdf_path(paper_id)
        pdf_verifier(pdf, paper.sha256)
        cropped = renderer(pdf, figure.page_number, figure.bbox)
        content = vision_requester(key, base_url, model, cropped)
        return {
            "figure_id": figure.id,
            "page_number": figure.page_number,
            "model": model,
            "description": content,
            "provenance": "AI_INFERRED_UNVERIFIED",
            "saved_to_card": False,
            "requires_pdf_verification": True,
        }


__all__ = [
    "VISION_MAX_COMPLETION_TOKENS",
    "VISION_RESPONSE_LIMIT_BYTES",
    "VisualService",
    "VisualServiceError",
    "extract_vision_response",
    "mask_secret",
    "read_text_content",
    "render_figure_crop",
    "request_vision_description",
    "usage_summary",
    "verify_pdf_integrity",
    "_safe_request_id",
]
