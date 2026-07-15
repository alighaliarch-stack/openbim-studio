"""
api/model.py

GET /api/model — full flat entity list (backs M10 -> frontend Model Context)
GET /api/tree   — spatial hierarchy (backs M02 Model Tree)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import get_entity_store
from api.schemas import ModelResponse, TreeResponse
from services.ifc.entity_store import EntityStore

router = APIRouter(tags=["model"])


@router.get("/model", response_model=ModelResponse)
async def get_model(store: EntityStore = Depends(get_entity_store)) -> ModelResponse:
    """Return every entity in the current model as a flat list."""
    entities = store.get_all_entities()
    return ModelResponse(entities=entities, count=len(entities))


@router.get("/tree", response_model=TreeResponse)
async def get_tree(store: EntityStore = Depends(get_entity_store)) -> TreeResponse:
    """Return the spatial hierarchy (Project -> Site -> Building -> ...)."""
    return TreeResponse(nodes=store.get_tree())
