"""
core/config.py

Central application configuration for the OpenBIM Studio MVP backend.

Per SDS Section 8.3 (Technology Stack) and SRS Section 11 (Deployment
Requirements), the MVP has no database and no cache: all persistence is a
single JSON file on disk (backend/data/model.json).

TODO(Phase 2): Replace flat settings object with pydantic-settings
`BaseSettings` once environment-specific configuration grows (multi-env,
secrets manager, etc).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """
    Immutable application settings, sourced from environment variables
    with sane MVP defaults (see backend/.env.example).
    """

    # --- General ---
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "true").lower() == "true")
    service_name: str = "OpenBIM Studio MVP"

    # --- Storage (M10 IFC Semantic Layer persistence) ---
    data_dir: Path = field(default_factory=lambda: BACKEND_ROOT / "data")
    # Day-25: MODEL_JSON_PATH overrides the model file only when explicitly
    # set (used by the workflow tests to run against a temporary model).
    model_json_path: Path = field(
        default_factory=lambda: Path(os.getenv("MODEL_JSON_PATH") or BACKEND_ROOT / "data" / "model.json")
    )

    # --- OpenSCAD Engine (M08) ---
    templates_dir: Path = field(default_factory=lambda: BACKEND_ROOT / "templates")
    scad_tmp_dir: Path = field(default_factory=lambda: Path(os.getenv("SCAD_TMP_DIR", "/tmp/openbim-scad")))
    openscad_binary: str = field(default_factory=lambda: os.getenv("OPENSCAD_BINARY", "openscad"))
    openscad_timeout_seconds: int = 30

    # --- AI Chat Module (M09) ---
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    llm_context_token_limit: int = 32_000  # FR-065: Context must fit LLM token limit (32k for MVP)

    # Day-29: local Ollama provider (LLM_PROVIDER=ollama), reached over its
    # HTTP API only; needs no API key. OLLAMA_MODEL can name any installed
    # model (e.g. gemma3:4b) without code changes.
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    # Generous: the first call after idle includes loading the model.
    ollama_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")))
    # Explicit context window; Ollama's default is smaller and silently
    # truncates longer prompts.
    ollama_num_ctx: int = 8192

    # --- CORS (frontend dev server) ---
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173",)


def get_settings() -> Settings:
    """
    Return the process-wide Settings instance.

    TODO(Phase 2): memoize with functools.lru_cache once settings are
    guaranteed immutable across the process lifetime in all deployment
    targets (currently trivial since Settings is frozen).
    """
    return Settings()
