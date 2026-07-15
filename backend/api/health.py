"""
api/health.py

GET /api/health — basic connectivity check used by the frontend on load
and by the Day-1 "Hello World" smoke test (SDS 9.1).
"""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import HealthResponse
from core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Return service status. Always 200 OK if the process is alive."""
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.service_name)
