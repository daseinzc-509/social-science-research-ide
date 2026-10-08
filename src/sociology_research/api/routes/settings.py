from fastapi import APIRouter, Depends

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import ModelSettingsUpdate, ModelSettingsView

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/models", response_model=ModelSettingsView)
def get_model_settings(services: ApplicationServices = Depends(get_services)):
    return services.settings.get_model_settings()


@router.put("/models", response_model=ModelSettingsView)
def update_model_settings(
    payload: ModelSettingsUpdate,
    services: ApplicationServices = Depends(get_services),
):
    return services.settings.save_model_settings(payload.model_dump(exclude_unset=True))
