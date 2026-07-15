"""
services/ifc/models.py

Data structures owned by the M10 IFC Semantic Layer.

Schema follows SDS Section 6.1 (Data Dictionary) / SRS Section 6.1 exactly:

    Attribute        Type              Description
    guid             UUID/str          Globally unique identifier
    type             Enum(str)         IFC type, e.g. "IfcWall"
    name             str               Human-readable name
    parent_guid      str | None        Parent entity GUID
    children_guids   list[str]         Child entity GUIDs
    psets            dict[str, PSet]   Property sets, e.g. {"Pset_WallCommon": {...}}
    geometry         GeometryRef|None  Geometry metadata (STL url + bounds)
    materials        list[str]         Material names
    layer            str               Discipline layer, e.g. "Architecture"
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IfcType(str, Enum):
    """
    Minimal IFC entity type enum covering the MVP component library
    (FR-053: wall, column, door) plus the spatial-structure types needed
    to build a Project -> Site -> Building -> Storey -> Element hierarchy.

    TODO(Phase 2): Replace with the full IFC4 entity type list, likely
    sourced from IfcOpenShell's schema definitions rather than hand-rolled.
    """

    PROJECT = "IfcProject"
    SITE = "IfcSite"
    BUILDING = "IfcBuilding"
    BUILDING_STOREY = "IfcBuildingStorey"
    SPACE = "IfcSpace"
    WALL = "IfcWall"
    COLUMN = "IfcColumn"
    DOOR = "IfcDoor"
    WINDOW = "IfcWindow"


class GeometryBounds(BaseModel):
    """Axis-aligned bounding box, in millimetres, matching FR-052."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]


class GeometryRef(BaseModel):
    """
    Reference to a rendered geometry asset for an entity.

    `type` is currently always "stl" (SRS 6.1); kept as a str for forward
    compatibility with other geometry encodings in later phases.
    """

    type: str = "stl"
    url: str
    bounds: GeometryBounds


# A property set is a flat map of property name -> value. Values may be
# bool | int | float | str per FR validation rules (numbers type-checked
# before persistence).
PropertySet = dict[str, Any]


class IfcEntity(BaseModel):
    """
    A single node in the IFC spatial/semantic graph.

    This is the canonical, wire-compatible representation used both for
    in-memory storage (M10) and for JSON persistence (model.json) and API
    responses (GET /api/model).
    """

    guid: str
    type: IfcType
    name: str
    parent_guid: str | None = None
    children_guids: list[str] = Field(default_factory=list)
    psets: dict[str, PropertySet] = Field(default_factory=dict)
    geometry: GeometryRef | None = None
    materials: list[str] = Field(default_factory=list)
    layer: str = "Architecture"


class SpatialNode(BaseModel):
    """
    A node in the derived spatial hierarchy tree (FR-072), built on demand
    from the flat entity map. Mirrors the IFC spatial-structure hierarchy:
    Project -> Site -> Building -> Storey -> Space -> Elements.
    """

    guid: str
    name: str
    type: IfcType
    children: list["SpatialNode"] = Field(default_factory=list)
    is_expanded: bool = True
    is_visible: bool = True


class ProjectMeta(BaseModel):
    """Top-level project metadata, matching model.json's "project" key."""

    guid: str
    name: str
    description: str = ""


class ModelDocument(BaseModel):
    """
    The full shape of model.json: project metadata + flat entity list.

    Used at load/save boundaries; the in-memory EntityStore converts this
    to/from its internal dict[guid, IfcEntity] representation.
    """

    project: ProjectMeta
    entities: list[IfcEntity] = Field(default_factory=list)


SpatialNode.model_rebuild()
