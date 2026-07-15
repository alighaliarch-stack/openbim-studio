"""
services/llm/models.py

Data structures owned by the M09 AI Chat Module (SDS Section 4, M09 —
"Data Structures Owned"), and the GUI Wireframe's TypeScript interfaces
mirrored here for the backend side of the contract:

    interface AIResponse {
        intent: "create_component" | "edit_property" | "query" | "explain";
        commands: Command[];
        text: string;
        suggestions?: string[];
    }
    interface Command {
        type: "CreateComponent" | "EditProperty" | "UpdateSelection";
        params: Record<string, any>;
    }
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(str, Enum):
    CREATE_COMPONENT = "create_component"
    EDIT_PROPERTY = "edit_property"
    QUERY = "query"
    EXPLAIN = "explain"


class CommandType(str, Enum):
    CREATE_COMPONENT = "CreateComponent"
    EDIT_PROPERTY = "EditProperty"
    UPDATE_SELECTION = "UpdateSelection"


class Command(BaseModel):
    """A single structured action parsed from an LLM response."""

    type: CommandType
    params: dict[str, Any] = Field(default_factory=dict)


class ModelContext(BaseModel):
    """
    Context handed to the LLM so it can ground its response in the current
    model state (FR-065: Context Building).

    Kept intentionally shallow for MVP: spatial tree + selection only.
    `to_prompt_json()` in ContextBuilder is responsible for keeping this
    under the 32k token budget.
    """

    spatial_tree: list[dict[str, Any]] = Field(default_factory=list)
    selected_guid: str | None = None
    selected_entity: dict[str, Any] | None = None
    active_layers: list[str] = Field(default_factory=list)


class AIRequest(BaseModel):
    """Full request payload sent to the LLM provider."""

    prompt: str
    model_context: ModelContext
    history: list[dict[str, str]] = Field(default_factory=list)


class AIResponse(BaseModel):
    """Parsed, validated response returned to the frontend (M09 -> M01)."""

    intent: Intent
    commands: list[Command] = Field(default_factory=list)
    text: str
    suggestions: list[str] = Field(default_factory=list)
