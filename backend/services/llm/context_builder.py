"""
services/llm/context_builder.py

M09 AI Chat Module — ContextBuilder (FR-065).

Builds the ModelContext handed to the LLM: spatial tree, selected entity,
active layers — formatted as JSON, excluding sensitive data (API keys),
and kept within the token budget.
"""

from __future__ import annotations

from services.ifc.entity_store import EntityStore
from services.llm.models import ModelContext


class ContextBuilder:
    """Builds a ModelContext snapshot from the current EntityStore state."""

    def __init__(self, entity_store: EntityStore, token_limit: int = 32_000) -> None:
        self._entity_store = entity_store
        self._token_limit = token_limit

    def build(self, selected_guid: str | None = None) -> ModelContext:
        """
        Assemble a ModelContext for the given selection.

        TODO: Enforce `self._token_limit` by truncating the spatial tree
        (e.g. collapsing deep/unselected branches) once models grow beyond
        trivial demo size; MVP models are small enough this is a no-op.
        """
        tree = [node.model_dump(mode="json") for node in self._entity_store.get_tree()]

        selected_entity = None
        if selected_guid:
            entity = self._entity_store.get_entity(selected_guid)
            selected_entity = entity.model_dump(mode="json")

        return ModelContext(
            spatial_tree=tree,
            selected_guid=selected_guid,
            selected_entity=selected_entity,
            active_layers=[],  # TODO(Phase 2): wire M03 Layer Tree once implemented
        )
