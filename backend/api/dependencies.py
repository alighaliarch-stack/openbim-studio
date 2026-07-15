"""
api/dependencies.py

FastAPI dependency-injection wiring.

The MVP is single-process/single-user, so every service below is a
process-wide singleton constructed once in main.py's lifespan handler and
stashed on `app.state`. These Depends()-based accessors just retrieve them
from the current Request, giving route handlers clean, testable signatures
without a full DI framework.
"""

from __future__ import annotations

from fastapi import Request

from services.ifc.entity_store import EntityStore
from services.llm.chat_service import AIChatService
from services.openscad.engine import OpenSCADEngine


def get_entity_store(request: Request) -> EntityStore:
    """Return the process-wide M10 EntityStore singleton."""
    return request.app.state.entity_store


def get_openscad_engine(request: Request) -> OpenSCADEngine:
    """Return the process-wide M08 OpenSCADEngine singleton."""
    return request.app.state.openscad_engine


def get_ai_chat_service(request: Request) -> AIChatService:
    """Return the process-wide M09 AIChatService singleton."""
    return request.app.state.ai_chat_service
