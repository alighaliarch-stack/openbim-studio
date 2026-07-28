"""
services/geometry/geometry_factory.py

M08 OpenSCAD Engine -- GeometryFactory.

Dispatches geometry regeneration to the appropriate per-IFC-type
generator, per the pipeline:

    Property Edit -> EntityStore -> ParametricGeometry -> GeometryFactory
    -> WallGenerator -> GeometryRef -> Frontend

GeometryFactory contains no geometry logic itself. Its only job is to
route an IfcEntity to the generator responsible for its IFC type and
return the resulting GeometryRef, or the entity's existing geometry
unchanged if no generator is registered for that type.

Currently supported:
    IfcWall -> WallGenerator
"""

from __future__ import annotations

from typing import Protocol

from services.geometry.wall_generator import WallGenerator
from services.ifc.models import GeometryRef, IfcEntity, IfcType


class _GeometryGenerator(Protocol):
    """Structural type describing a per-IFC-type geometry generator.

    Any class exposing a matching `generate` staticmethod can be
    registered in `_GENERATORS` below, without needing to inherit from
    this class explicitly.
    """

    @staticmethod
    def generate(entity: IfcEntity) -> GeometryRef | None:
        ...


# TODO (Phase 2):
# Register additional generators here as they are implemented, e.g.
# BeamGenerator, SlabGenerator, DoorGenerator, WindowGenerator, etc.
_GENERATORS: dict[IfcType, type[_GeometryGenerator]] = {
    IfcType.WALL: WallGenerator,
}


class GeometryFactory:
    """Routes geometry regeneration requests to the correct generator.

    The generator to use is selected via `_GENERATORS`, keyed by
    `entity.type`. Types without a registered generator pass through
    with their existing geometry unchanged.
    """

    @staticmethod
    def regenerate(entity: IfcEntity) -> GeometryRef | None:
        """Regenerate geometry for `entity`, dispatched by its IFC type.

        Args:
            entity: The entity whose geometry should be (re)generated.

        Returns:
            A `GeometryRef` describing the regenerated geometry, or the
            entity's existing `geometry` unchanged if its type has no
            registered generator.
        """
        generator = _GENERATORS.get(entity.type)
        if generator is None:
            return entity.geometry

        return generator.generate(entity)