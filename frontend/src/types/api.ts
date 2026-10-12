/**
 * types/api.ts
 *
 * REST API request/response DTOs, mirroring backend/api/schemas.py
 * (SDS Section 9.1 REST Endpoints).
 */

import type { AIResponse } from "./commands";
import type { GeometryBounds, IfcEntity, SpatialNode } from "./entities";

export interface HealthResponse {
  status: string;
  service: string;
}

export interface ModelResponse {
  entities: IfcEntity[];
  count: number;
}

export interface TreeResponse {
  nodes: SpatialNode[];
}

export interface PropertyEditRequest {
  pset_name: string;
  property_name: string;
  value: unknown;
}

export interface PropertyEditResponse {
  success: boolean;
  entity: IfcEntity;
  /**
   * Day-20: non-silent signal that the property was persisted but the
   * downstream OpenSCAD/STL regeneration did not produce new geometry.
   * null when regeneration succeeded or wasn't applicable to this entity.
   */
  geometry_warning: string | null;
}

export interface ScadRenderRequest {
  component_type: string;
  parameters: Record<string, unknown>;
}

export interface ScadRenderResponse {
  success: boolean;
  stl_path: string | null;
  bounds: GeometryBounds | null;
  render_time_ms: number | null;
  error: string | null;
}

export interface AIPromptRequest {
  prompt: string;
  selected_guid: string | null;
}

/** Day-31: POST /api/ai/confirm — only the proposal id and the current selection. */
export interface AIConfirmRequest {
  proposal_id: string;
  selected_guid: string | null;
}

export interface AIConfirmResponse {
  success: boolean;
  text: string;
  entity: IfcEntity;
}

/** Re-exported so callers can `import type { AIResponse } from "@/types/api"`. */
export type { AIResponse };

export interface ErrorResponse {
  success: false;
  error: string;
  detail?: string;
}
