from __future__ import annotations
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any
from core.persistence import ModelPersistence
from services.ifc.geometry_inputs import GEOMETRY_INPUTS, dimension_error, drives_geometry
from services.ifc.models import IfcEntity, ModelDocument, SpatialNode

logger = logging.getLogger(__name__)
ChangeCallback = Callable[[str, str, dict[str, Any]], None]

class EntityNotFoundError(KeyError):
    pass

class EditRejectionReason(str, Enum):
    VALIDATION_FAILED = "validation_failed"
    NO_EXISTING_GEOMETRY = "no_existing_geometry"
    GEOMETRY_REGENERATION_FAILED = "geometry_regeneration_failed"
    PERSISTENCE_FAILED = "persistence_failed"

class PropertyEditRejected(RuntimeError):
    """A property edit was not applied. The committed model (memory and disk) is unchanged."""

    def __init__(self, reason: EditRejectionReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail

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

    def _persist(self, entities: dict[str, IfcEntity] | None = None) -> None:
        # Day-28: `entities` lets set_prop() save a candidate model before
        # committing it to memory; defaults to the committed model.
        entities = self._entities if entities is None else entities
        document = ModelDocument(
            project={"guid": self._project_guid, "name": self._project_name, "description": ""},
            entities=list(entities.values()),
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

        Day-28: atomic (copy-on-write). The edit is applied to a deep copy
        of the entity; the live entity is never mutated. For a property
        that drives geometry, the value is validated and geometry is
        regenerated on the copy; the candidate model is then persisted
        atomically, and only after that succeeds does the copy replace the
        live entity and subscribers get a "property_edited" event.

        Any rejection raises PropertyEditRejected and leaves the committed
        model unchanged in memory and on disk (ModelPersistence.save()
        never replaces model.json with a partial write). Properties that
        don't drive geometry are saved without regenerating it.

        Raises:
            EntityNotFoundError: unknown guid.
            PropertyEditRejected: validation, regeneration or persistence failed.
        """
        current = self.get_entity(guid)
        regenerate = drives_geometry(current.type, pset, prop)
        if regenerate:
            self._validate_geometry_edit(current, pset, prop, value)

        candidate = current.model_copy(deep=True)
        candidate.psets.setdefault(pset, {})[prop] = value

        if regenerate:
            self._check_geometry_inputs(candidate)
            self._regenerate_geometry(candidate, current)

        candidate_entities = {**self._entities, guid: candidate}
        try:
            self._persist(candidate_entities)
        except Exception as exc:
            logger.exception("Persisting edit of %s.%s on %s failed", pset, prop, guid)
            raise PropertyEditRejected(
                EditRejectionReason.PERSISTENCE_FAILED, f"model.json could not be written: {exc}"
            ) from exc

        self._entities = candidate_entities
        self._notify(guid, "property_edited", {"pset": pset, "prop": prop, "value": value})
        return candidate

    @staticmethod
    def _validate_geometry_edit(entity: IfcEntity, pset: str, prop: str, value: Any) -> None:
        problem = dimension_error(value)
        if problem:
            raise PropertyEditRejected(EditRejectionReason.VALIDATION_FAILED, f"{pset}.{prop} {problem}")
        if entity.geometry is None:
            raise PropertyEditRejected(
                EditRejectionReason.NO_EXISTING_GEOMETRY,
                f"'{entity.guid}' has no existing geometry to regenerate from {pset}.{prop}",
            )

    @staticmethod
    def _check_geometry_inputs(candidate: IfcEntity) -> None:
        # The generator needs every input of the edited entity, not just the
        # edited one; a missing/invalid stored value is reported as such
        # rather than as a geometry-kernel failure.
        for pset, props in GEOMETRY_INPUTS.get(candidate.type, {}).items():
            for prop in sorted(props):
                if prop not in candidate.psets.get(pset, {}):
                    raise PropertyEditRejected(
                        EditRejectionReason.VALIDATION_FAILED, f"{pset}.{prop} is missing on '{candidate.guid}'"
                    )
                problem = dimension_error(candidate.psets[pset][prop])
                if problem:
                    raise PropertyEditRejected(
                        EditRejectionReason.VALIDATION_FAILED, f"stored {pset}.{prop} {problem}"
                    )

    @staticmethod
    def _regenerate_geometry(candidate: IfcEntity, current: IfcEntity) -> None:
        from services.geometry.parametric_geometry import ParametricGeometry
        try:
            ParametricGeometry.regenerate(candidate)
        except Exception as exc:
            logger.exception("Geometry regeneration for %s raised", candidate.guid)
            raise PropertyEditRejected(
                EditRejectionReason.GEOMETRY_REGENERATION_FAILED, f"geometry regeneration raised: {exc}"
            ) from exc
        # Generators never raise on expected failures (invalid inputs,
        # OpenSCAD error, timeout, bad STL); they return the previous
        # GeometryRef unchanged, while every successful render gets a new
        # uuid-named STL url (see wall_generator.py / cli_executor.py).
        if candidate.geometry is None or candidate.geometry.url == current.geometry.url:
            raise PropertyEditRejected(
                EditRejectionReason.GEOMETRY_REGENERATION_FAILED, "OpenSCAD did not produce new geometry"
            )

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
        # Day-28: runs after a change is committed, so a failing subscriber
        # is logged rather than turning a committed edit into an error.
        for callback in self._subscribers.get(guid, []):
            try:
                callback(guid, change_type, payload)
            except Exception:
                logger.exception("Subscriber for %s failed on %s", guid, change_type)

    def _build_tree(self) -> list[SpatialNode]:
        def build_node(entity: IfcEntity) -> SpatialNode:
            return SpatialNode(
                guid=entity.guid, name=entity.name, type=entity.type,
                children=[build_node(self._entities[c]) for c in entity.children_guids if c in self._entities],
            )
        roots = [e for e in self._entities.values() if not e.parent_guid]
        return [build_node(r) for r in roots]