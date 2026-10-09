"""
main.py

OpenBIM Studio MVP — backend entrypoint.

Run with:
    cd backend
    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    python main.py
    # Serves on http://localhost:8000

Wires up the process-wide singletons for the 3 backend modules in MVP
scope (M10 IFC Semantic Layer, M08 OpenSCAD Engine, M09 AI Chat Module)
and mounts the REST API router.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.router import api_router
from core.config import get_settings
from core.persistence import ModelPersistence
from services.ifc.entity_store import EntityStore
from services.llm.chat_service import AIChatService
from services.llm.context_builder import ContextBuilder
from services.llm.llm_client import LLMClient
from services.llm.output_parser import OutputParser
from services.openscad.cli_executor import CLIExecutor
from services.openscad.component_registry import ComponentRegistry
from services.openscad.engine import OpenSCADEngine, set_current_engine
from services.openscad.stl_parser import StlParser
from services.openscad.template_renderer import TemplateRenderer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Construct and tear down process-wide singletons.

    Runs once on startup (before the app accepts requests) and once on
    shutdown. Services are stashed on `app.state` and retrieved via
    api/dependencies.py Depends() functions.
    """
    settings = get_settings()

    # --- M10 IFC Semantic Layer ---
    persistence = ModelPersistence(settings.model_json_path)
    entity_store = EntityStore(persistence)
    try:
        entity_store.load()
    except FileNotFoundError:
        logger.warning(
            "No model.json found at %s; starting with an empty model. "
            "TODO: seed backend/data/model.json with a sample building.",
            settings.model_json_path,
        )

    # --- M08 OpenSCAD Engine ---
    registry = ComponentRegistry()
    renderer = TemplateRenderer(settings.templates_dir)
    executor = CLIExecutor(
        settings.openscad_binary, settings.scad_tmp_dir, settings.openscad_timeout_seconds
    )
    stl_parser = StlParser()
    openscad_engine = OpenSCADEngine(registry, renderer, executor, stl_parser)

    # --- M09 AI Chat Module ---
    context_builder = ContextBuilder(entity_store, settings.llm_context_token_limit)
    api_keys = {"anthropic": settings.anthropic_api_key, "openai": settings.openai_api_key}
    llm_client = LLMClient(
        settings.llm_provider,
        api_keys.get(settings.llm_provider),  # Day-29: none for "ollama"
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        num_ctx=settings.ollama_num_ctx,
    )
    output_parser = OutputParser()
    ai_chat_service = AIChatService(context_builder, llm_client, output_parser, entity_store)

    app.state.entity_store = entity_store
    app.state.openscad_engine = openscad_engine
    set_current_engine(openscad_engine)
    app.state.ai_chat_service = ai_chat_service

    logger.info("%s ready.", settings.service_name)
    yield
    logger.info("%s shutting down.", settings.service_name)


def create_app() -> FastAPI:
    """Application factory — builds and configures the FastAPI instance."""
    settings = get_settings()

    app = FastAPI(title=settings.service_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    # Serve the OpenSCAD engine's rendered STL output over HTTP so
    # GeometryRef.url values (e.g. "/geometry/<uuid>.stl") are directly
    # fetchable by the frontend's THREE.STLLoader. Same directory the
    # engine already writes to (settings.scad_tmp_dir) -- no second
    # storage system. StaticFiles requires the directory to exist at
    # mount time, so create it here (CLIExecutor also does this on
    # first render, but that happens later, during lifespan startup).
    settings.scad_tmp_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/geometry", StaticFiles(directory=str(settings.scad_tmp_dir)), name="geometry")

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
