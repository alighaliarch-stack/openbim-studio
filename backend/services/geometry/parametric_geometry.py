"""
services/geometry/parametric_geometry.py

Parametric geometry orchestration.

Delegates geometry regeneration to GeometryFactory, which dispatches by
IFC type to the appropriate generator (currently WallGenerator for
IfcWall). This restores the documented pipeline:

    EntityStore -> ParametricGeometry -> GeometryFactory -> WallGenerator

Phase 1 (superseded): this module previously duplicated wall
bounding-box arithmetic inline instead of delegating to GeometryFactory.
That duplication has been removed -- GeometryFactory/WallGenerator are
now the single source of truth for geometry computation.

Phase 2: WallGenerator will be extended to call OpenSCADEngine instead
of computing a bounding box directly. No change is required in this
file when that happens, since this module only delegates.
"""

from services.geometry.geometry_factory import GeometryFactory
from services.ifc.models import IfcEntity


class ParametricGeometry:

    @staticmethod
    def regenerate(entity: IfcEntity) -> None:
        entity.geometry = GeometryFactory.regenerate(entity)