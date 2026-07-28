"""services/geometry/wall_generator.py

M08 OpenSCAD Engine -- WallGenerator.

Produces updated geometry metadata (a `GeometryRef` with a recalculated
bounding box) for `IfcWall` entities, derived from their editable
quantities (Length, Height, Thickness) in `Qto_WallBaseQuantities`.

This module has exactly one responsibility: turning wall quantities into
bounding-box dimensions. It performs no file I/O, no OpenSCAD template
rendering, and no STL generation -- those concerns belong to later
stages of the M08 pipeline (TemplateRenderer, CLIExecutor, StlParser)
and are deliberately out of scope here. Because `WallGenerator` only
depends on `IfcEntity`/`GeometryRef` and exposes the single
`generate(entity) -> GeometryRef | None` contract that `GeometryFactory`
relies on, this bounding-box implementation can later be replaced with a
true OpenSCAD-backed generator without changing any caller.

Axis convention: X=Length, Y=Thickness, Z=Height. This matches the
`cube([length, thickness, height])` convention used by the OpenSCAD
templates this module will eventually delegate to (see
services/openscad/template_renderer.py), so the bounding box a caller
sees today lines up with the real geometry that later replaces it.
"""

from __future__ import annotations

from typing import NamedTuple

from services.ifc.models import GeometryRef, IfcEntity

_QUANTITIES_PSET = "Qto_WallBaseQuantities"
_LENGTH_PROP = "Length"
_HEIGHT_PROP = "Height"
_THICKNESS_PROP = "Thickness"


class _WallQuantities(NamedTuple):
    """Validated wall quantities, keyed by name to prevent axis mix-ups.

    Deliberately not a bare positional tuple: `generate()` reads
    `.length`/`.height`/`.thickness` explicitly when building the
    bounding box, so the X/Y/Z axis mapping is visible at the point of
    use instead of depending on remembering a tuple's field order.
    """

    length: float
    height: float
    thickness: float


class WallGenerator:
    """Generates bounding-box geometry metadata for `IfcWall` entities.

    Reads the wall's editable quantities (Length, Height, Thickness) from
    `Qto_WallBaseQuantities` and produces a new `GeometryRef` with an
    updated bounding box. The geometry's `type` and `url` are always
    preserved unchanged; only `bounds` is recalculated.
    """

    @staticmethod
    def generate(entity: IfcEntity) -> GeometryRef | None:
        """Regenerate bounding-box geometry for `entity`.

        Args:
            entity: The `IfcWall` entity to generate geometry for.

        Returns:
            A new `GeometryRef` with recalculated bounds if `entity` has
            existing geometry and valid, positive numeric Length,
            Height, and Thickness quantities. Otherwise, `entity.geometry`
            is returned unchanged (no validation error is raised).
        """
        geometry = entity.geometry
        quantities = WallGenerator._read_quantities(entity)
        if geometry is None or quantities is None:
            return entity.geometry

        # X=Length, Y=Thickness, Z=Height -- named-field access, not
        # positional, so this mapping cannot be silently swapped.
        new_bounds = geometry.bounds.model_copy(
            update={
                "min": (0.0, 0.0, 0.0),
                "max": (quantities.length, quantities.thickness, quantities.height),
            }
        )
        return geometry.model_copy(update={"bounds": new_bounds})

    @staticmethod
    def _read_quantities(entity: IfcEntity) -> _WallQuantities | None:
        """Validate and extract wall quantities from `entity`.

        Validation requires:
            - `entity.geometry` exists.
            - `entity.geometry.bounds` exists.
            - `Length`, `Height`, and `Thickness` are all present in
              `entity.psets["Qto_WallBaseQuantities"]`.
            - Each value is numeric and strictly positive.

        Returns:
            A `_WallQuantities` named tuple if every check above passes;
            otherwise `None`.
        """
        geometry = entity.geometry
        if geometry is None or getattr(geometry, "bounds", None) is None:
            return None

        pset = entity.psets.get(_QUANTITIES_PSET)
        if pset is None:
            return None

        length = WallGenerator._positive_number(pset.get(_LENGTH_PROP))
        height = WallGenerator._positive_number(pset.get(_HEIGHT_PROP))
        thickness = WallGenerator._positive_number(pset.get(_THICKNESS_PROP))

        if length is None or height is None or thickness is None:
            return None

        return _WallQuantities(length=length, height=height, thickness=thickness)

    @staticmethod
    def _positive_number(value: object) -> float | None:
        """Return `value` as a float if it is a strictly positive number.

        Booleans are explicitly rejected even though `bool` is a
        subclass of `int` in Python, since a True/False value is never a
        meaningful wall dimension.

        Args:
            value: The raw property value to validate.

        Returns:
            `value` as a `float` if it is a positive `int` or `float`
            (and not a `bool`); otherwise `None`.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value <= 0:
            return None
        return float(value)