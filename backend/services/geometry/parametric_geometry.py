"""
services/geometry/parametric_geometry.py

Temporary parametric geometry engine.

Phase 1:
Regenerates placeholder bounding boxes from editable IFC quantities.

Phase 2:
This module will generate OpenSCAD instead.
"""

from services.ifc.models import IfcEntity


class ParametricGeometry:

    @staticmethod
    def regenerate(entity: IfcEntity) -> None:

        if entity.type != "IfcWall":
            return

        quantities = entity.psets.get("Qto_WallBaseQuantities", {})

        length = quantities.get("Length")
        height = quantities.get("Height")
        thickness = quantities.get("Thickness")

        if None in (length, height, thickness):
            return

        entity.geometry.bounds.min = [0, 0, 0]

        entity.geometry.bounds.max = [
            length,
            thickness,
            height,
        ]