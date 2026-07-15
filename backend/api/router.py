"""
api/router.py

Aggregates all endpoint routers under a single `/api` prefix, mounted
once in main.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from api import ai, health, model, properties, scad

api_router = APIRouter(prefix="/api")

api_router.include_router(health.router)
api_router.include_router(model.router)
api_router.include_router(properties.router)
api_router.include_router(scad.router)
api_router.include_router(ai.router)
