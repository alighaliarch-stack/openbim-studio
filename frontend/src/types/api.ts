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

/** Re-exported so callers can `import type { AIResponse } from "@/types/api"`. */
export type { AIResponse };

export interface ErrorResponse {
  success: false;
  error: string;
  detail?: string;
}
