"""FastAPI dependency accessors for the application-service composition root."""

from __future__ import annotations

from fastapi import Request

from ..services.application import ApplicationServices


def get_services(request: Request) -> ApplicationServices:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise RuntimeError("application services are not initialized")
    return services
