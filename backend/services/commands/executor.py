"""
services/commands/executor.py

Day-27 minimal command execution bridge (layer 4).

execute_command() always validates first (services/commands/contract.py)
and then makes exactly one call: EntityStore.set_prop() -- the same call
POST /api/properties/{guid} makes. Geometry is therefore regenerated only
by the existing deterministic chain:

    EntityStore.set_prop -> ParametricGeometry -> GeometryFactory
    -> WallGenerator -> OpenSCADEngine -> OpenSCAD -> STL -> GeometryRef

This module deliberately imports nothing from services.openscad or
services.geometry and never calls /api/scad/render, so it cannot become a
second geometry path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from services.commands.contract import ValidatedPropertyEdit, validate_command
from services.ifc.entity_store import EntityStore
from services.ifc.models import IfcEntity
from services.llm.models import Command


@dataclass(frozen=True)
class CommandExecutionResult:
    edit: ValidatedPropertyEdit
    entity: IfcEntity
    # Day-28: set_prop() is atomic and raises PropertyEditRejected when
    # regeneration fails, so a returned result always has new geometry and
    # this is always True. Kept for compatibility.
    geometry_regenerated: bool


def execute_command(command: Command | Mapping[str, Any], store: EntityStore) -> CommandExecutionResult:
    """
    Validate `command` and, only if accepted, apply it through
    EntityStore.set_prop().

    Raises:
        CommandRejected: if validation fails; nothing is changed.
        PropertyEditRejected: (services.ifc.entity_store) if regeneration
            or persistence fails; nothing is changed (Day-28 atomic edits).
    """
    edit = validate_command(command, store)

    geometry_before = store.get_entity(edit.target_guid).geometry
    url_before = geometry_before.url if geometry_before else None

    entity = store.set_prop(edit.target_guid, edit.pset, edit.property, edit.value_mm)

    url_after = entity.geometry.url if entity.geometry else None
    return CommandExecutionResult(
        edit=edit,
        entity=entity,
        geometry_regenerated=url_after is not None and url_after != url_before,
    )
