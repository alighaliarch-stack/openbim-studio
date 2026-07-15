"""
services/ifc/entity_store.py

M10 IFC Semantic Layer — EntityStore.

Owns the canonical in-memory entity graph (MODEL_STATE in SDS 10.2):

    MODEL_STATE = {
        "entities": { guid: IfcEntity, ... },
        "tree": None  # built on demand via _build_tree()
    }

Public API mirrors SRS FR-070:
    getEntity(guid), getTree(), getRelationships(guid),
    setProp(), addEntity(), removeEntity(), subscribe(guid, callback)

Guarantee (SDS 8.2): spatial tree is always valid; no orphaned nodes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.persistence import ModelPersistence
from services.ifc.models import IfcEntity, ModelDocument, SpatialNode
from services.geometry.parametric_geometry import ParametricGeometry

logger = logging.getLogger(__name__)

# Subscriber callback signature: (guid, change_type, payload) -> None
ChangeCallback = Callable[[str, str, dict[str, Any]], None]


class EntityNotFoundError(KeyError):
    """Raised when an operation references a GUID not present in the store."""


class EntityStore:
    """
    Single source of truth for all IFC entity data, relationships, property
    sets, and spatial structure, for the lifetime of the backend process.

    Not thread-safe by design (MVP is single-user, single-process); the
    FastAPI app should treat this as a process-wide singleton (see
    api/router.py / main.py dependency wiring).
    """

    def __init__(self, persistence: ModelPersistence) -> None:
        self._persistence = persistence
        self._entities: dict[str, IfcEntity] = {}
        self._tree_cache: list[SpatialNode] | None = None
        self._subscribers: dict[str, list[ChangeCallback]] = {}
        self._project_guid: str = ""
        self._project_name: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """
        FR-071: Load model.json on startup, populate the entity store, and
        build the spatial tree. Raises if the file is missing or corrupt.
        """
        raw = self._persistence.load()
        document = ModelDocument.model_validate(raw)

        self._project_guid = document.project.guid
        self._project_name = document.project.name
        self._entities = {e.guid: e for e in document.entities}
        self._tree_cache = None  # force rebuild on next getTree()

        logger.info(
            "Loaded model '%s': %d entities", self._project_name, len(self._entities)
        )

    def _persist(self) -> None:
        """Write current in-memory state back to model.json (write path)."""
        document = ModelDocument(
            project={
                "guid": self._project_guid,
                "name": self._project_name,
                "description": "",
            },
            entities=list(self._entities.values()),
        )
        self._persistence.save(document.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_entity(self, guid: str) -> IfcEntity:
        """Return the entity for `guid` or raise EntityNotFoundError."""
        try:
            return self._entities[guid]
        except KeyError as exc:
            raise EntityNotFoundError(guid) from exc

    def get_all_entities(self) -> list[IfcEntity]:
        """Return all entities as a flat list (backs GET /api/model)."""
        return list(self._entities.values())

    def get_tree(self) -> list[SpatialNode]:
        """
        FR-072: Return the spatial hierarchy, building it from the flat
        entity map if not already cached.
        """
        if self._tree_cache is None:
            self._tree_cache = self._build_tree()
        return self._tree_cache

    def get_relationships(self, guid: str) -> dict[str, list[str]]:
        """
        Return the immediate relationships of `guid`: its parent (if any)
        and its children, mirroring IFC aggregation/containment relations.

        TODO(Phase 2): Model IfcRelAggregates / IfcRelContainedInSpatial
        Structure explicitly instead of deriving purely from parent/child
        GUID fields.
        """
        entity = self.get_entity(guid)
        return {
            "parent": [entity.parent_guid] if entity.parent_guid else [],
            "children": list(entity.children_guids),
        }

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set_prop(self, guid: str, pset: str, prop: str, value: Any) -> IfcEntity:
        """
        FR-073 / Flow 1 step "M10: Update entity in-memory".

        Updates a single property within a PSet, persists to disk, and
        notifies subscribers with a "property_edited" change event.
        """
        entity = self.get_entity(guid)
        entity.psets.setdefault(pset, {})[prop] = value
        ParametricGeometry.regenerate(entity)
        self._persist()
        self._notify(guid, "property_edited", {"pset": pset, "prop": prop, "value": value})
        return entity

    def add_entity(self, entity: IfcEntity) -> IfcEntity:
        """
        Add a new entity to the graph (e.g. from AI-driven CreateComponent
        commands, Flow 2), wiring it into its parent's children_guids and
        invalidating the tree cache so the hierarchy stays valid.
        """
        if entity.guid in self._entities:
            raise ValueError(f"Entity with guid {entity.guid} already exists")

        self._entities[entity.guid] = entity

        if entity.parent_guid:
            parent = self.get_entity(entity.parent_guid)
            if entity.guid not in parent.children_guids:
                parent.children_guids.append(entity.guid)

        self._tree_cache = None
        self._persist()
        self._notify(entity.guid, "entity_added", {"entity": entity.model_dump(mode="json")})
        return entity

    def remove_entity(self, guid: str) -> None:
        """
        Remove an entity and detach it from its parent's children list.

        TODO(Phase 2): Decide cascade behaviour for entities with children
        (currently: children become orphaned parent_guid references, which
        violates the "no orphaned nodes" guarantee -- must be fixed before
        this is used outside of trivial demo flows).
        """
        entity = self.get_entity(guid)

        if entity.parent_guid and entity.parent_guid in self._entities:
            parent = self._entities[entity.parent_guid]
            if guid in parent.children_guids:
                parent.children_guids.remove(guid)

        del self._entities[guid]
        self._tree_cache = None
        self._persist()
        self._notify(guid, "entity_removed", {})

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    def subscribe(self, guid: str, callback: ChangeCallback) -> None:
        """Register `callback` to be notified of changes to `guid`."""
        self._subscribers.setdefault(guid, []).append(callback)

    def _notify(self, guid: str, change_type: str, payload: dict[str, Any]) -> None:
        for callback in self._subscribers.get(guid, []):
            callback(guid, change_type, payload)

    # ------------------------------------------------------------------
    # Internal: spatial tree construction
    # ------------------------------------------------------------------

    def _build_tree(self) -> list[SpatialNode]:
        """
        FR-072: Construct the spatial hierarchy from the flat entity map.
        Roots are entities with no parent_guid.
        """

        def build_node(entity: IfcEntity) -> SpatialNode:
            return SpatialNode(
                guid=entity.guid,
                name=entity.name,
                type=entity.type,
                children=[
                    build_node(self._entities[child_guid])
                    for child_guid in entity.children_guids
                    if child_guid in self._entities
                ],
            )

        roots = [e for e in self._entities.values() if not e.parent_guid]
        return [build_node(root) for root in roots]
