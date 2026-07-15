"""
services/openscad/models.py

Data structures owned by the M08 OpenSCAD Engine (SDS Section 4, M08 —
"Data Structures Owned"):

    ComponentTemplate {id, name, scadSource, paramSchema: JSONSchema, ifcType, psetMapping}
    RenderJob {id, status, template, params, result}
    ParameterMap {[name: string]: number|string|bool}
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from services.ifc.models import IfcType

# A ParameterMap is a flat name -> value dict passed to the Jinja2 template
# and to the OpenSCAD engine's parameter validator.
ParameterMap = dict[str, Any]


class ParamField(BaseModel):
    """
    JSON-Schema-lite description of a single component parameter, used to
    validate incoming ParameterMaps (FR: "Component parameters must match
    template schema, e.g. wall length must be >= 0").
    """

    name: str
    type: str  # "number" | "string" | "boolean"
    default: Any
    minimum: float | None = None
    maximum: float | None = None


class ComponentTemplate(BaseModel):
    """
    A registered parametric SCAD component (FR-053: wall, column, door
    minimum for MVP).
    """

    id: str
    name: str
    template_filename: str  # e.g. "wall.scad.j2", resolved under templates/
    param_schema: list[ParamField]
    ifc_type: IfcType
    pset_mapping: dict[str, str] = Field(default_factory=dict)


class RenderStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class GeometryBounds(BaseModel):
    """Axis-aligned bounding box computed from the parsed STL (FR-052)."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]


class RenderResult(BaseModel):
    """
    Result of a full render() call: template -> .scad -> OpenSCAD CLI ->
    .stl -> parsed bounds. Returned to M06 Geometry Manager via the
    /api/scad/render response.
    """

    status: RenderStatus
    stl_path: str | None = None
    bounds: GeometryBounds | None = None
    render_time_ms: float | None = None
    error: str | None = None


class RenderJob(BaseModel):
    """
    Tracks a single render request end-to-end. Kept in-memory only for
    MVP (no RenderQueue/Celery — see SDS full-architecture doc for the
    Phase 2 async job queue).
    """

    id: str
    component_id: str
    params: ParameterMap
    status: RenderStatus = RenderStatus.PENDING
    result: RenderResult | None = None
