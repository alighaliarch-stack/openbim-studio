"""
api/properties.py

POST /api/properties/{guid} — edit a single PSet property on an entity.
Backs the M05 Property Manager -> M10 IFC Semantic Layer write path
(SDS Flow 1). For a property that drives geometry, EntityStore.set_prop()
regenerates it server-side within this same request:

    EntityStore.set_prop -> ParametricGeometry -> GeometryFactory
    -> WallGenerator -> OpenSCADEngine -> OpenSCAD -> STL -> GeometryRef

Day-28: edits are atomic. set_prop() either commits the property together
with its regenerated geometry (memory and model.json), or raises
PropertyEditRejected and changes nothing. Rejections are returned as a
structured ErrorResponse (SDS 9.3) with a machine-readable `code`:

    422  validation_failed             invalid wall dimension (backend-authoritative bound)
    422  no_existing_geometry          wall dimension edit on a wall without geometry
    502  geometry_regeneration_failed  OpenSCAD/STL pipeline did not produce new geometry
    500  persistence_failed            model.json could not be written

PropertyEditResponse.geometry_warning (Day 20) is kept for compatibility;
with atomic edits a committed edit never has stale geometry, so it is
always null.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_entity_store
from api.schemas import ErrorResponse, PropertyEditRequest, PropertyEditResponse
from services.ifc.entity_store import (
    EditRejectionReason,
    EntityNotFoundError,
    EntityStore,
    PropertyEditRejected,
)

router = APIRouter(tags=["properties"])

# Rejection reason -> (HTTP status, user-facing error text).
_REJECTIONS: dict[EditRejectionReason, tuple[int, str]] = {
    EditRejectionReason.VALIDATION_FAILED: (422, "Invalid value; the change was not saved."),
    EditRejectionReason.NO_EXISTING_GEOMETRY: (
        422, "This wall has no geometry to regenerate; the change was not saved."
    ),
    EditRejectionReason.GEOMETRY_REGENERATION_FAILED: (
        502, "Geometry could not be regenerated; the change was not saved."
    ),
    EditRejectionReason.PERSISTENCE_FAILED: (500, "The model could not be saved; the change was not applied."),
}


@router.post(
    "/properties/{guid}",
    response_model=PropertyEditResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def edit_property(
    guid: str,
    body: PropertyEditRequest,
    store: EntityStore = Depends(get_entity_store),
) -> PropertyEditResponse:
    """
    Update `pset_name.property_name` = `value` on entity `guid`, atomically.

    TODO: Type-check values of properties that don't drive geometry
    (SDS 9.3 Validation Rules); wall dimensions are validated in set_prop().
    """
    try:
        entity = store.set_prop(guid, body.pset_name, body.property_name, body.value)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(error="Entity not found", detail=str(exc)).model_dump(),
        ) from exc
    except PropertyEditRejected as exc:
        status_code, error = _REJECTIONS[exc.reason]
        raise HTTPException(
            status_code=status_code,
            detail=ErrorResponse(error=error, detail=exc.detail, code=exc.reason.value).model_dump(),
        ) from exc

    return PropertyEditResponse(success=True, entity=entity, geometry_warning=None)
