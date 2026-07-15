/**
 * types/entities.ts
 *
 * IFC entity/tree types, mirroring backend/services/ifc/models.py exactly
 * (SDS Section 6.1 Data Dictionary). Kept in sync manually for MVP.
 *
 * TODO(Phase 2): Generate this file from the backend Pydantic models
 * (e.g. via datamodel-code-generator / openapi-typescript against
 * GET /openapi.json) instead of hand-mirroring, per the SDS Appendix
 * "packages/shared-types" convention.
 */

export type IfcType =
  | "IfcProject"
  | "IfcSite"
  | "IfcBuilding"
  | "IfcBuildingStorey"
  | "IfcSpace"
  | "IfcWall"
  | "IfcColumn"
  | "IfcDoor"
  | "IfcWindow";

/** A flat property-value map within a single PSet, e.g. Pset_WallCommon. */
export type PropertySet = Record<string, unknown>;

export interface GeometryBounds {
  min: [number, number, number];
  max: [number, number, number];
}

export interface GeometryRef {
  type: "stl";
  url: string;
  bounds: GeometryBounds;
}

/** Canonical entity representation — matches model.json / GET /api/model. */
export interface IfcEntity {
  guid: string;
  type: IfcType;
  name: string;
  parent_guid: string | null;
  children_guids: string[];
  psets: Record<string, PropertySet>;
  geometry: GeometryRef | null;
  materials: string[];
  layer: string;
}

/** Node in the derived spatial hierarchy tree — matches GET /api/tree. */
export interface SpatialNode {
  guid: string;
  name: string;
  type: IfcType;
  children: SpatialNode[];
  is_expanded: boolean;
  is_visible: boolean;
}
