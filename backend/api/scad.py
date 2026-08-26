"""
api/scad.py

POST /api/scad/render — render a parametric component to STL + bounds.
Backs the M08 OpenSCAD Engine (SDS Flow 1, second hop: property edit
triggers a re-render, or the frontend calls this directly for a new
component preview).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_openscad_engine
from api.schemas import ScadRenderRequest, ScadRenderResponse
from services.openscad.engine import OpenSCADEngine
from services.openscad.models import RenderStatus

router = APIRouter(tags=["scad"])


@router.post("/scad/render", response_model=ScadRenderResponse)
async def render_component(
    body: ScadRenderRequest,
    engine: OpenSCADEngine = Depends(get_openscad_engine),
) -> ScadRenderResponse:
    """
    Render `component_type` (e.g. "wall") with `parameters` and return the
    resulting STL path + bounding box, or a descriptive error.

    Always returns 200; success/failure is signalled via the `success`
    field so the frontend can show a friendly "Geometry generation failed"
    message per the SRS alternative flow, without special-casing HTTP
    status codes for this endpoint.
    """
    result = engine.render(body.component_type, body.parameters)
    return ScadRenderResponse(
        success=result.status == RenderStatus.SUCCESS,
        stl_path=result.stl_path,
        bounds=result.bounds,
        render_time_ms=result.render_time_ms,
        error=result.error,
    )
