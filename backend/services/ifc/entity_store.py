from __future__ import annotations
import logging
from collections.abc import Callable
from typing import Any
from core.persistence import ModelPersistence
from services.geometry.parametric_geometry import ParametricGeometry
from services.ifc.models import IfcEntity, ModelDocument, SpatialNode

logger = logging.getLogger(__name__)
ChangeCallback = Callable[[str, str, dict[str, Any]], None]

class EntityNotFoundError(KeyError):
    pass

class EntityStore:
    def __init__(self, persistence: ModelPersistence) -> None:
        self._persistence = persistence
        self._entities: dict[str, IfcEntity] = {}
        self._tree_cache: list[SpatialNode] | None = None
        self._subscribers: dict[str, list[ChangeCallback]] = {}
        self._project_guid: str = ""
        self._project_name: str = ""

    def load(self) -> None:
        raw = self._persistence.load()
        document = ModelDocument.model_validate(raw)
        self._project_guid = document.project.guid
        self._project_name = document.project.name
        self._entities = {e.guid: e for e in document.entities}
        self._tree_cache = None

    def _persist(self) -> None:
        document = ModelDocument(
            project={"guid": self._project_guid, "name": self._project_name, "description": ""},
            entities=list(self._entities.values()),
        )
        self._persistence.save(document.model_dump(mode="json"))

    def get_entity(self, guid: str) -> IfcEntity:
        try:
            return self._entities[guid]
        except KeyError as exc:
            raise EntityNotFoundError(guid) from exc

    def get_all_entities(self) -> list[IfcEntity]:
        return list(self._entities.values())

    def get_tree(self) -> list[SpatialNode]:
        if self._tree_cache is None:
            self._tree_cache = self._build_tree()
        return self._tree_cache

    def get_relationships(self, guid: str) -> dict[str, list[str]]:
        entity = self.get_entity(guid)
        return {"parent": [entity.parent_guid] if entity.parent_guid else [], "children": list(entity.children_guids)}

    def set_prop(self, guid: str, pset: str, prop: str, value: Any) -> IfcEntity:
        """
        FR-073 / Flow 1 step "M10: Update entity in-memory".

        Updates a single property within a PSet, regenerates the
        entity's geometry to reflect the new value, persists to disk,
        and notifies subscribers with a "property_edited" change event.

        Execution order matters: geometry is regenerated before persist
        so the on-disk model.json never captures stale geometry from
        before this edit, and before notify so subscribers reacting to
        the event see the regenerated geometry, not the pre-edit value.
        """
        entity = self.get_entity(guid)
        entity.psets.setdefault(pset, {})[prop] = value
        ParametricGeometry.regenerate(entity)
        self._persist()
        self._notify(guid, "property_edited", {"pset": pset, "prop": prop, "value": value})
        return entity

    def add_entity(self, entity: IfcEntity) -> IfcEntity:
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

    def subscribe(self, guid: str, callback: ChangeCallback) -> None:
        self._subscribers.setdefault(guid, []).append(callback)

    def _notify(self, guid: str, change_type: str, payload: dict[str, Any]) -> None:
        for callback in self._subscribers.get(guid, []):
            callback(guid, change_type, payload)

    def _build_tree(self) -> list[SpatialNode]:
        def build_node(entity: IfcEntity) -> SpatialNode:
            return SpatialNode(
                guid=entity.guid, name=entity.name, type=entity.type,
                children=[build_node(self._entities[c]) for c in entity.children_guids if c in self._entities],
            )
        roots = [e for e in self._entities.values() if not e.parent_guid]
        return [build_node(r) for r in roots]