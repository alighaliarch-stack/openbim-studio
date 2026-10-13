"""
services/commands/contract.py

Day-27 AI Command Contract -- structured command representation and
command validation.

Research architecture, four separate layers:
    1. natural-language interpretation (LLM)       -- NOT implemented here
    2. structured command representation           -- this module
    3. command validation                          -- this module
    4. command execution                           -- services/commands/executor.py

Reuses the existing M09 command envelope (services/llm/models.py
`Command`: {"type": CommandType, "params": {...}}) instead of adding a
second command model. The only command accepted is:

    {
      "type": "EditProperty",
      "params": {
        "target_guid": "<guid of an existing IfcWall>",
        "pset": "Qto_WallBaseQuantities",
        "property": "Length" | "Height" | "Thickness",
        "value": <finite number>,
        "unit": "mm" | "m"
      }
    }

The value is normalised to millimetres and must be > 0 and <= 100 m.
Anything else is rejected with CommandRejected(reason, detail); a request
is never converted into a different command. Validation reads the
EntityStore but never mutates it, and depends on no LLM, so it can be
tested on its own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from services.ifc.entity_store import EntityNotFoundError, EntityStore
from services.ifc.geometry_inputs import MAX_DIMENSION_MM as _MAX_DIMENSION_MM
from services.ifc.models import IfcType
from services.llm.models import Command, CommandType

# IFC type -> property set -> editable properties. Deliberately only the
# three wall quantities WallGenerator turns into geometry.
EDITABLE_PROPERTIES: dict[IfcType, dict[str, frozenset[str]]] = {
    IfcType.WALL: {"Qto_WallBaseQuantities": frozenset({"Length", "Height", "Thickness"})},
}

# Accepted length units -> millimetre factor. Exact, case-sensitive names
# only ("M" is not "m"); every other unit is rejected, never guessed.
UNIT_TO_MM: dict[str, Decimal] = {"mm": Decimal(1), "m": Decimal(1000)}

# Upper bound on a normalised dimension (100 m), so an absurd value can't
# reach OpenSCAD. Day-28: shared with EntityStore.set_prop(), which applies
# the same bound to every wall dimension edit (services/ifc/geometry_inputs.py).
MAX_DIMENSION_MM = Decimal(_MAX_DIMENSION_MM)


class RejectionReason(str, Enum):
    MALFORMED_COMMAND = "malformed_command"
    UNSUPPORTED_COMMAND = "unsupported_command"
    UNKNOWN_ENTITY = "unknown_entity"
    UNSUPPORTED_TARGET_TYPE = "unsupported_target_type"
    UNSUPPORTED_PROPERTY = "unsupported_property"
    PROPERTY_NOT_PRESENT = "property_not_present"
    INVALID_VALUE = "invalid_value"
    UNSUPPORTED_UNIT = "unsupported_unit"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    # Day-30 numeric/unit provenance (services/commands/provenance.py).
    NO_EXPLICIT_QUANTITY = "no_explicit_quantity"
    AMBIGUOUS_QUANTITY = "ambiguous_quantity"
    RELATIVE_CHANGE_UNSUPPORTED = "relative_change_unsupported"
    QUANTITY_MISMATCH = "quantity_mismatch"
    # Day-30 hardening: property and target provenance (same module).
    NO_EXPLICIT_PROPERTY = "no_explicit_property"
    AMBIGUOUS_PROPERTY = "ambiguous_property"
    PROPERTY_MISMATCH = "property_mismatch"
    TARGET_MISMATCH = "target_mismatch"
    # Day-31 MVP target policy: every AI edit needs a selected target wall.
    NO_SELECTION = "no_selection"
    # Day-31: the target property changed after the proposal was made.
    STALE_PROPOSAL = "stale_proposal"
    # Day-34: the request names an element other than the target wall.
    UNSUPPORTED_ENTITY_REFERENCE = "unsupported_entity_reference"
    CONFLICTING_TARGET_REFERENCE = "conflicting_target_reference"


class CommandRejected(ValueError):
    """A command failed validation. Nothing was executed or changed."""

    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class EditPropertyParams(BaseModel):
    """Exact `params` shape of an EditProperty command; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_guid: str
    pset: str
    property: str
    value: Any  # type-checked by _to_millimetres() for explicit rejection reasons
    unit: str


class ValidatedPropertyEdit(BaseModel):
    """An accepted command, normalised and ready for execution."""

    model_config = ConfigDict(frozen=True)

    target_guid: str
    pset: str
    property: str
    value_mm: int | float
    source_value: int | float
    source_unit: str


def validate_command(command: Command | Mapping[str, Any], store: EntityStore) -> ValidatedPropertyEdit:
    """
    Validate a structured command against the contract and the current
    model.

    Accepts an existing `Command` (e.g. from OutputParser) or a raw dict.

    Raises:
        CommandRejected: with the first reason the command is not accepted.
    """
    command = _parse_envelope(command)

    if command.type != CommandType.EDIT_PROPERTY:
        raise CommandRejected(
            RejectionReason.UNSUPPORTED_COMMAND,
            f"'{command.type.value}' is not supported; only EditProperty is accepted",
        )

    try:
        params = EditPropertyParams.model_validate(command.params)
    except ValidationError as exc:
        raise CommandRejected(RejectionReason.MALFORMED_COMMAND, _summarise(exc)) from exc

    try:
        entity = store.get_entity(params.target_guid)
    except EntityNotFoundError as exc:
        raise CommandRejected(
            RejectionReason.UNKNOWN_ENTITY, f"no entity with guid '{params.target_guid}'"
        ) from exc

    editable = EDITABLE_PROPERTIES.get(entity.type)
    if editable is None:
        raise CommandRejected(
            RejectionReason.UNSUPPORTED_TARGET_TYPE,
            f"'{params.target_guid}' is {entity.type.value}; only IfcWall can be edited",
        )

    if params.property not in editable.get(params.pset, frozenset()):
        allowed = ", ".join(f"{pset}.{prop}" for pset, props in editable.items() for prop in sorted(props))
        raise CommandRejected(
            RejectionReason.UNSUPPORTED_PROPERTY,
            f"'{params.pset}.{params.property}' is not editable; allowed: {allowed}",
        )

    # Edit an existing property only; never create a new one.
    if params.property not in entity.psets.get(params.pset, {}):
        raise CommandRejected(
            RejectionReason.PROPERTY_NOT_PRESENT,
            f"'{params.target_guid}' has no {params.pset}.{params.property} to edit",
        )

    return ValidatedPropertyEdit(
        target_guid=params.target_guid,
        pset=params.pset,
        property=params.property,
        value_mm=_to_millimetres(params.value, params.unit),
        source_value=params.value,
        source_unit=params.unit,
    )


def _parse_envelope(command: Command | Mapping[str, Any]) -> Command:
    if isinstance(command, Command):
        return command
    if not isinstance(command, Mapping):
        raise CommandRejected(RejectionReason.MALFORMED_COMMAND, "command must be an object")

    unexpected = set(command) - {"type", "params"}
    if unexpected:
        raise CommandRejected(
            RejectionReason.MALFORMED_COMMAND, f"unexpected command fields: {sorted(unexpected)}"
        )

    command_type = command.get("type")
    if not isinstance(command_type, str):
        raise CommandRejected(RejectionReason.MALFORMED_COMMAND, "command 'type' must be a string")
    try:
        CommandType(command_type)
    except ValueError as exc:
        raise CommandRejected(
            RejectionReason.UNSUPPORTED_COMMAND,
            f"'{command_type}' is not supported; only EditProperty is accepted",
        ) from exc

    try:
        return Command.model_validate(dict(command))
    except ValidationError as exc:
        raise CommandRejected(RejectionReason.MALFORMED_COMMAND, _summarise(exc)) from exc


def _to_millimetres(value: Any, unit: str) -> int | float:
    # bool is an int subclass; strings (even "3000" or "3m") are not numbers here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejected(
            RejectionReason.INVALID_VALUE, f"value must be a number, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise CommandRejected(RejectionReason.INVALID_VALUE, f"value must be finite, got {value}")

    factor = UNIT_TO_MM.get(unit)
    if factor is None:
        raise CommandRejected(
            RejectionReason.UNSUPPORTED_UNIT,
            f"unit '{unit}' is not supported; use one of {sorted(UNIT_TO_MM)}",
        )

    # Decimal avoids binary-float artefacts (2.8 * 1000 == 2800.0000000000005).
    millimetres = Decimal(repr(value)) * factor
    if millimetres <= 0:
        raise CommandRejected(RejectionReason.VALUE_OUT_OF_RANGE, f"value must be greater than zero, got {value} {unit}")
    if millimetres > MAX_DIMENSION_MM:
        raise CommandRejected(
            RejectionReason.VALUE_OUT_OF_RANGE,
            f"value must be at most {MAX_DIMENSION_MM} mm, got {millimetres} mm",
        )
    return int(millimetres) if millimetres == millimetres.to_integral_value() else float(millimetres)


def _summarise(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'command'}: {error['msg']}" for error in exc.errors()
    )
