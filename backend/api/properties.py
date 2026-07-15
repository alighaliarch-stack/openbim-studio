"""
api/properties.py

POST /api/properties/{guid} — edit a single PSet property on an entity.
Backs the M05 Property Manager -> M10 IFC Semantic Layer write path
(SDS Flow 1, first hop; the SCAD re-render hop is a separate frontend-
initiated call to POST /api/scad/render).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_entity_store
from api.schemas import ErrorResponse, PropertyEditRequest, PropertyEditResponse
from services.ifc.entity_store import EntityNotFoundError, EntityStore

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
        entity = store.set_prop(guid, body.pset_name, body.property_name, body.value)
    except EntityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(error="Entity not found", detail=str(exc)).model_dump(),
        ) from exc

    return PropertyEditResponse(success=True, entity=entity)
