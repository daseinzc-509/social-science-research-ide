"""Optional PDF formula/vision settings and explicitly requested figure review.

薄 HTTP 适配层：核心视觉配置、单图裁剪与多模态请求统一收拢在 VisualService 和
OpenAICompatibleClient 中；此处保留若干兼容符号以支撑既有单元测试桩注入。
"""
from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ...config import Settings, save_local_environment
from ...services.application import ApplicationServices
from ...services.visual import (
    VISION_MAX_COMPLETION_TOKENS,
    VISION_RESPONSE_LIMIT_BYTES,
    VisualService,
    VisualServiceError,
    extract_vision_response as _service_extract_vision_response,
    mask_secret as _mask,
    read_text_content as _read_text_content,
    render_figure_crop as _service_render_figure,
    request_vision_description as _service_request_vision,
    usage_summary as _usage_summary,
    verify_pdf_integrity as _service_verify_pdf,
    _safe_request_id,
)
from ..dependencies import get_services
from ..schemas import AnalyzeFigureResponse, VisualSettingsResponse

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


_DEFAULT_URLOPEN = urlopen


def _visual_service(services: ApplicationServices) -> VisualService:
    existing = getattr(services, "visual", None)
    if existing is not None:
        return existing
    return VisualService(services.repository, services.papers)


def _render_figure(pdf: Path, page_no: int, bounds: tuple[float, float, float, float]) -> bytes:
    try:
        return _service_render_figure(pdf, page_no, bounds)
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _verify_pdf(pdf: Path, sha256: str) -> None:
    try:
        _service_verify_pdf(pdf, sha256)
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _extract_vision_response(reply: object, *, model: str, request_id: str = "") -> str:
    try:
        return _service_extract_vision_response(reply, model=model, request_id=request_id)
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _request_vision(key: str, url: str, model: str, image_bytes: bytes) -> str:
    # 若单元测试 monkeypatch 了本模块的 urlopen，则以兼容 sender 注入；否则走统一 AI 代理 opener
    sender = urlopen if urlopen is not _DEFAULT_URLOPEN else None
    try:
        return _service_request_vision(key, url, model, image_bytes, sender=sender)
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@settings_router.get("", response_model=VisualSettingsResponse)
def get_visual_settings():
    service = VisualService(None, None)  # type: ignore[arg-type]
    return service.get_settings(settings_loader=Settings.from_environment)


@settings_router.put("", response_model=VisualSettingsResponse)
def set_visual_settings(update: VisualSettingsUpdate):
    service = VisualService(None, None)  # type: ignore[arg-type]
    try:
        return service.update_settings(
            formula_enabled=bool(update.formula_enabled),
            vision_enabled=bool(update.vision_enabled),
            use_pro_connection=bool(update.use_pro_connection),
            vision_api_key=update.vision_api_key,
            vision_api_base_url=update.vision_api_base_url,
            vision_model=update.vision_model,
            settings_loader=Settings.from_environment,
            env_saver=save_local_environment,
        )
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@figure_router.get("/{figure_id}/preview")
def preview_figure(
    paper_id: str,
    figure_id: str,
    services: ApplicationServices = Depends(get_services),
):
    """Local-only, zero-token preview of exactly what can later be sent."""
    try:
        image = _visual_service(services).preview_figure(
            paper_id,
            figure_id,
            renderer=_render_figure,
        )
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@figure_router.post("/{figure_id}/analyze", response_model=AnalyzeFigureResponse)
def analyze_figure(
    paper_id: str,
    figure_id: str,
    payload: AnalyzeFigureRequest,
    services: ApplicationServices = Depends(get_services),
):
    try:
        result = _visual_service(services).analyze_figure(
            paper_id,
            figure_id,
            approve_external_send=payload.approve_external_send,
            settings_loader=Settings.from_environment,
            pdf_verifier=_verify_pdf,
            renderer=_render_figure,
            vision_requester=_request_vision,
        )
    except VisualServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    # HTTP 边界再次锁定单图视觉描述的来源属性，确保绝不伪装成已核验原文或回写 Paper Card
    validated = AnalyzeFigureResponse(
        **{**result, "provenance": "AI_INFERRED_UNVERIFIED", "saved_to_card": False}
    )
    return JSONResponse(
        validated.model_dump(mode="json"),
        headers={"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"},
    )
