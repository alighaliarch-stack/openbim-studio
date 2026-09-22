"""
api/properties.py

POST /api/properties/{guid} — edit a single PSet property on an entity.
Backs the M05 Property Manager -> M10 IFC Semantic Layer write path
(SDS Flow 1, first hop; the SCAD re-render hop is a separate frontend-
initiated call to POST /api/scad/render).

Day-20 addition: PropertyEditResponse.geometry_warning. EntityStore.
set_prop() always persists the property, then attempts geometry
regeneration; if that regeneration fails (invalid quantities, OpenSCAD
CLI error, timeout), WallGenerator never raises -- it just returns the
entity's previous GeometryRef unchanged (see wall_generator.py), so the
route would otherwise return success:true with no signal that geometry
didn't actually update. Detected here by comparing GeometryRef.url
before/after the edit; EntityStore/GeometryFactory/WallGenerator are
untouched.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_entity_store
from api.schemas import ErrorResponse, PropertyEditRequest, PropertyEditResponse
from services.ifc.entity_store import EntityNotFoundError, EntityStore
from services.ifc.models import GeometryRef, IfcType

router = APIRouter(tags=["properties"])


@router.post(
    "/properties/{guid}",
    response_model=PropertyEditResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
)
async def edit_property(
    guid: str,
    body: PropertyEditRequest,
    store: EntityStore = Depends(get_entity_store),
) -> PropertyEditResponse:
    """
    Update `pset_name.property_name` = `value` on entity `guid`.

    TODO: Validate `value` against the property's expected type before
    persisting (SDS 9.3 Validation Rules: "Property values must be
    type-checked before persistence").
    """
    try:
        before = store.get_entity(guid)
        geometry_before = before.geometry
        entity_type = before.type
        entity = store.set_prop(guid, body.pset_name, body.property_name, body.value)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(error="Entity not found", detail=str(exc)).model_dump(),
        ) from exc

    geometry_warning = _detect_geometry_regeneration_failure(
        entity_type, geometry_before, entity.geometry
    )

    return PropertyEditResponse(success=True, entity=entity, geometry_warning=geometry_warning)


def _detect_geometry_regeneration_failure(
    entity_type: IfcType,
    geometry_before: GeometryRef | None,
    geometry_after: GeometryRef | None,
) -> str | None:
    """
    Best-effort, additive-only signal that a property edit persisted but
    did not produce new geometry.

    Every SUCCESSFUL WallGenerator render writes a freshly-generated STL
    filename into GeometryRef.url (CLIExecutor mints a new uuid per
    render -- see cli_executor.py's job_id). WallGenerator's failure
    paths (invalid/missing quantities, template error, CLI failure,
    timeout) never raise; they return the entity's previous GeometryRef
    completely unchanged -- see wall_generator.py. So an unchanged url
    after editing a wall that already had geometry means regeneration
    was attempted and did not produce new geometry.

    Scoped to IfcType.WALL only, since WallGenerator is currently the
    only registered generator (services/geometry/geometry_factory.py).
    Other entity types are never flagged here, since for them
    GeometryFactory.regenerate() is an expected no-op passthrough, not a
    failure -- broaden this check if/when more generators are registered.
    """
    if entity_type != IfcType.WALL or geometry_before is None:
        return None
    if geometry_after is None or geometry_after.url == geometry_before.url:
        return (
            "The property was saved, but the wall geometry could not be "
            "regenerated. The viewport is still showing the previous geometry."
        )
    return None
