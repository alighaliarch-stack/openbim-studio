"""services/geometry/wall_generator.py

M08 OpenSCAD Engine -- WallGenerator.

Produces updated geometry metadata (a `GeometryRef` with real geometry
generated through the OpenSCAD pipeline) for `IfcWall` entities, derived
from their editable quantities (Length, Height, Thickness) in
`Qto_WallBaseQuantities`.

Axis convention: X=Length, Y=Thickness, Z=Height -- matches the
`cube([length, thickness, height])` convention used by wall.scad.j2.

Failure behavior: if quantities are missing/invalid, or the OpenSCAD
render fails for any reason, `entity.geometry` is returned unchanged.
No fallback geometry is fabricated in either case.
"""

from __future__ import annotations

from typing import NamedTuple

from services.ifc.models import GeometryRef, IfcEntity
from services.openscad.engine import get_current_engine
from services.openscad.models import ParameterMap, RenderStatus

_QUANTITIES_PSET = "Qto_WallBaseQuantities"
_LENGTH_PROP = "Length"
_HEIGHT_PROP = "Height"
_THICKNESS_PROP = "Thickness"

_COMPONENT_ID = "wall"


class _WallQuantities(NamedTuple):
    length: float
    height: float
    thickness: float


class WallGenerator:
    @staticmethod
    def generate(entity: IfcEntity) -> GeometryRef | None:
        geometry = entity.geometry
        quantities = WallGenerator._read_quantities(entity)
        if geometry is None or quantities is None:
            return entity.geometry

        params = WallGenerator._to_parameter_map(quantities)
        engine = get_current_engine()
        result = engine.render(_COMPONENT_ID, params)

        if result.status != RenderStatus.SUCCESS or result.bounds is None:
            return entity.geometry

        new_bounds = geometry.bounds.model_copy(
            update={"min": result.bounds.min, "max": result.bounds.max}
        )
        return geometry.model_copy(
            update={"bounds": new_bounds, "url": result.stl_path}
        )

    @staticmethod
    def _read_quantities(entity: IfcEntity) -> _WallQuantities | None:
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

        return _WallQuantities(
            length=length,
            height=height,
            thickness=thickness,
        )

    @staticmethod
    def _positive_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value <= 0:
            return None
        return float(value)

    @staticmethod
    def _to_parameter_map(quantities: _WallQuantities) -> ParameterMap:
        return {
            "length": quantities.length,
            "height": quantities.height,
            "thickness": quantities.thickness,
        }