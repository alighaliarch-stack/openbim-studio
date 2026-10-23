"""
services/openscad/component_registry.py

M08 OpenSCAD Engine — ComponentRegistry.

Provides the built-in SCAD component library (FR-053): wall, column, door
at minimum. Each component defines its parameter schema, target IFC type,
and PSet mapping used when M09/M10 create new entities from a render.
"""

from __future__ import annotations

from services.ifc.models import IfcType
from services.openscad.models import ComponentTemplate, ParamField


class ComponentNotFoundError(KeyError):
    """Raised when a requested component id is not registered."""


class ComponentRegistry:
    """
    In-memory registry of available ComponentTemplates.

    TODO(Phase 2): Load templates from packages/scad-components (per SDS
    Appendix repository structure) instead of a hardcoded dict, and support
    hot-reload / user-contributed components.
    """

    def __init__(self) -> None:
        self._components: dict[str, ComponentTemplate] = {}
        self._register_builtin_components()

    def get(self, component_id: str) -> ComponentTemplate:
        """Return the ComponentTemplate for `component_id` or raise."""
        try:
            return self._components[component_id]
        except KeyError as exc:
            raise ComponentNotFoundError(component_id) from exc

    def list_components(self) -> list[ComponentTemplate]:
        """Return all registered components (backs GET /api/components)."""
        return list(self._components.values())

    def register(self, template: ComponentTemplate) -> None:
        """Register or overwrite a component template."""
        self._components[template.id] = template

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _register_builtin_components(self) -> None:
        self.register(
            ComponentTemplate(
                id="wall",
                name="Wall",
                template_filename="wall.scad.j2",
                ifc_type=IfcType.WALL,
                pset_mapping={
                    "length": "Qto_WallBaseQuantities.Length",
                    "height": "Qto_WallBaseQuantities.Height",
                    "thickness": "Qto_WallBaseQuantities.Thickness",
                },
                param_schema=[
                    ParamField(name="length", type="number", default=5000, minimum=0),
                    ParamField(name="height", type="number", default=3000, minimum=0),
                    ParamField(name="thickness", type="number", default=200, minimum=0),
                    # Day-52C: world position of the wall's min corner (mm),
                    # from OpenBIM_Placement; 0 keeps the origin placement.
                    ParamField(name="x", type="number", default=0),
                    ParamField(name="y", type="number", default=0),
                    ParamField(name="z", type="number", default=0),
                ],
            )
        )
        self.register(
            ComponentTemplate(
                id="column",
                name="Column",
                template_filename="column.scad.j2",
                ifc_type=IfcType.COLUMN,
                pset_mapping={
                    "diameter": "Qto_ColumnBaseQuantities.Diameter",
                    "height": "Qto_ColumnBaseQuantities.Height",
                },
                param_schema=[
                    ParamField(name="diameter", type="number", default=300, minimum=0),
                    ParamField(name="height", type="number", default=3000, minimum=0),
                ],
            )
        )
        self.register(
            ComponentTemplate(
                id="door",
                name="Door",
                template_filename="door.scad.j2",
                ifc_type=IfcType.DOOR,
                pset_mapping={
                    "width": "Qto_DoorBaseQuantities.Width",
                    "height": "Qto_DoorBaseQuantities.Height",
                    "thickness": "Qto_DoorBaseQuantities.Thickness",
                },
                param_schema=[
                    ParamField(name="width", type="number", default=900, minimum=0),
                    ParamField(name="height", type="number", default=2100, minimum=0),
                    ParamField(name="thickness", type="number", default=40, minimum=0),
                ],
            )
        )
