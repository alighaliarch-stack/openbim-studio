"""
services/ifc/geometry_inputs.py

Day-28: which semantic properties drive geometry, and the backend-
authoritative rule for their values.

Shared by EntityStore.set_prop() (every property edit, from the API or
the command executor) and the Day-27 command contract, so there is one
definition of the editable wall dimensions and one 0 < value <= 100 m
bound. Values here are millimetres; unit conversion belongs to callers
(the command contract normalises "m" before execution).

Day-52C adds the optional wall placement coordinates (PLACEMENT_INPUTS,
|value| <= 100 m). The AI command contract does not include them.

Lives in the semantic layer (not services/geometry) because it only
describes IFC properties; it imports nothing that generates geometry.
"""

from __future__ import annotations

import math

from services.ifc.models import IfcType

# IFC type -> property set -> properties whose value determines geometry.
# Deliberately only the three wall quantities WallGenerator reads.
GEOMETRY_INPUTS: dict[IfcType, dict[str, frozenset[str]]] = {
    IfcType.WALL: {"Qto_WallBaseQuantities": frozenset({"Length", "Height", "Thickness"})},
}

# Day-52C: wall position in world millimetres, edited by the viewport Move
# tool. An MVP convention, NOT IFC ObjectPlacement: a property set that
# drives geometry (OpenSCAD translates the wall). Unlike GEOMETRY_INPUTS
# these are optional -- a missing set or value means 0, so walls without
# it stay at the origin.
PLACEMENT_PSET = "OpenBIM_Placement"
PLACEMENT_INPUTS: dict[IfcType, dict[str, frozenset[str]]] = {
    IfcType.WALL: {PLACEMENT_PSET: frozenset({"X", "Y", "Z"})},
}

# MVP safety bound (Day 27 decision): an application-level limit, not an
# IFC, structural or physical maximum.
MAX_DIMENSION_MM = 100_000
# Day-52C: same application-level bound for coordinates, either side of 0.
MAX_COORDINATE_MM = 100_000


def drives_geometry(entity_type: IfcType, pset: str, prop: str) -> bool:
    """True if editing `pset.prop` on an `entity_type` must regenerate geometry."""
    return is_placement(entity_type, pset, prop) or prop in GEOMETRY_INPUTS.get(entity_type, {}).get(pset, frozenset())


def is_placement(entity_type: IfcType, pset: str, prop: str) -> bool:
    """True if `pset.prop` is one of `entity_type`'s optional placement coordinates."""
    return prop in PLACEMENT_INPUTS.get(entity_type, {}).get(pset, frozenset())


def geometry_value_error(entity_type: IfcType, pset: str, prop: str, value: object) -> str | None:
    """The rule for a geometry-driving property: coordinates may be <= 0, dimensions may not."""
    if is_placement(entity_type, pset, prop):
        return coordinate_error(value)
    return dimension_error(value)


def coordinate_error(value: object) -> str | None:
    """
    Return why `value` is not a valid coordinate in millimetres, or None
    if it is: a finite JSON number (not a boolean) with
    |value| <= MAX_COORDINATE_MM. Zero and negative values are valid.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"must be a number, got {type(value).__name__}"
    if isinstance(value, float) and not math.isfinite(value):
        return f"must be finite, got {value}"
    if abs(value) > MAX_COORDINATE_MM:
        return f"must be between -{MAX_COORDINATE_MM} and {MAX_COORDINATE_MM} mm, got {value}"
    return None


def dimension_error(value: object) -> str | None:
    """
    Return why `value` is not a valid dimension in millimetres, or None
    if it is: a finite JSON number (not a boolean) with
    0 < value <= MAX_DIMENSION_MM.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"must be a number, got {type(value).__name__}"
    # Only floats can be non-finite; math.isfinite() would raise
    # OverflowError on an int too large for a float (still rejected below).
    if isinstance(value, float) and not math.isfinite(value):
        return f"must be finite, got {value}"
    if value <= 0:
        return f"must be greater than 0 mm, got {value}"
    if value > MAX_DIMENSION_MM:
        return f"must be at most {MAX_DIMENSION_MM} mm, got {value}"
    return None
