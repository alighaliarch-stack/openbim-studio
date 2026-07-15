"""
api/schemas.py

Request/response DTOs for the REST API layer, kept separate from the
domain models in services/*/models.py so the wire contract can evolve
independently of internal representations.

Error shape matches SDS Section 9.3:
    { "success": false, "error": "...", "detail": "..." }
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from services.ifc.models import IfcEntity, SpatialNode
from services.llm.models import AIResponse
from services.openscad.models import GeometryBounds


class HealthResponse(BaseModel):
    status: str
    service: str


class ModelResponse(BaseModel):
    """GET /api/model response body."""

    entities: list[IfcEntity]
    count: int


class TreeResponse(BaseModel):
    """GET /api/tree response body."""

    nodes: list[SpatialNode]


class PropertyEditRequest(BaseModel):
    """POST /api/properties/{guid} request body."""

    pset_name: str
    property_name: str
    value: Any


class PropertyEditResponse(BaseModel):
    """POST /api/properties/{guid} response body."""

    success: bool
    entity: IfcEntity


class ScadRenderRequest(BaseModel):
    """POST /api/scad/render request body."""

    component_type: str
    parameters: dict[str, Any]


class ScadRenderResponse(BaseModel):
    """POST /api/scad/render response body."""

    success: bool
    stl_path: str | None = None
    bounds: GeometryBounds | None = None
    render_time_ms: float | None = None
    error: str | None = None


class AIPromptRequest(BaseModel):
    """POST /api/ai/prompt request body."""

    prompt: str
    selected_guid: str | None = None


class ErrorResponse(BaseModel):
    """Standard error envelope (SDS 9.3)."""

    success: bool = False
    error: str
    detail: str | None = None


# Re-exported for convenience so route modules can `from api.schemas import AIResponse`.
__all__ = [
    "AIPromptRequest",
    "AIResponse",
    "ErrorResponse",
    "HealthResponse",
    "ModelResponse",
    "PropertyEditRequest",
    "PropertyEditResponse",
    "ScadRenderRequest",
    "ScadRenderResponse",
    "TreeResponse",
]
