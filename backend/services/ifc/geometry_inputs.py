"""
services/ifc/geometry_inputs.py

Day-28: which semantic properties drive geometry, and the backend-
authoritative rule for their values.

Shared by EntityStore.set_prop() (every property edit, from the API or
the command executor) and the Day-27 command contract, so there is one
definition of the editable wall dimensions and one 0 < value <= 100 m
bound. Values here are millimetres; unit conversion belongs to callers
(the command contract normalises "m" before execution).

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

# MVP safety bound (Day 27 decision): an application-level limit, not an
# IFC, structural or physical maximum.
MAX_DIMENSION_MM = 100_000


def drives_geometry(entity_type: IfcType, pset: str, prop: str) -> bool:
    """True if editing `pset.prop` on an `entity_type` must regenerate geometry."""
    return prop in GEOMETRY_INPUTS.get(entity_type, {}).get(pset, frozenset())


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
