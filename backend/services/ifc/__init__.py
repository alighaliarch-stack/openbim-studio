"""
services/ifc — M10 IFC Semantic Layer.

In-memory IFC entity graph. Single source of truth for all entity data,
relationships, property sets, and spatial structure (SDS Section 8.4).

Public contract (per SDS 8.2):
  Input:      setProp, addEntity, removeEntity calls
  Output:     ChangeEvent published to subscribers
  Guarantee:  Spatial tree always valid; all relationships maintained
"""

from services.ifc.models import IfcEntity, PropertySet, SpatialNode
from services.ifc.entity_store import EntityStore

__all__ = ["EntityStore", "IfcEntity", "PropertySet", "SpatialNode"]
