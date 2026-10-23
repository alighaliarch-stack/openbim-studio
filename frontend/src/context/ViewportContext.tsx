/**
 * context/ViewportContext.tsx
 *
 * M06 Geometry Manager viewport state (SDS 10.2 "Frontend React Context"):
 *
 *   {
 *     cameraPosition: [number, number, number];
 *     cameraTarget: [number, number, number];
 *     zoom: number;
 *     geometryLoaded: Record<string, boolean>;
 *   }
 *
 * This context holds *serializable* viewport state only. The live
 * Three.js scene/camera/renderer objects are owned imperatively by
 * components/viewport/SceneManager.ts, not by React state (SDS M01
 * "ViewportContext {camera, renderer, scene}" refers to that imperative
 * handle, distinct from this state snapshot).
 *
 * Day-52C: also holds the active toolbar tool (Toolbar sets it, Viewport
 * acts on it) and a short Move status message shown in the toolbar.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import type { IfcEntity } from "@/types/entities";

export type ToolMode = "select" | "move" | "rotate" | "draw";

export interface MoveStatus {
  kind: "info" | "error";
  text: string;
}

/**
 * Day-52C: wall position (world mm) as edited by the Move tool. An MVP
 * convention, not IFC ObjectPlacement; mirrors backend
 * services/ifc/geometry_inputs.py (missing set/value = 0).
 */
export const PLACEMENT_PSET = "OpenBIM_Placement";
export type MoveAxis = "X" | "Y";

/** The stored coordinate (0 when absent), or null if the stored value isn't a finite number. */
export function placementCoordinate(entity: IfcEntity, axis: MoveAxis): number | null {
  const value = entity.psets[PLACEMENT_PSET]?.[axis] ?? 0;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The wall Move may act on: exactly one selected IfcWall with geometry and valid X/Y, else null. */
export function moveTarget(selectedEntities: IfcEntity[]): IfcEntity | null {
  if (selectedEntities.length !== 1) return null;
  const [entity] = selectedEntities;
  if (entity.type !== "IfcWall" || !entity.geometry) return null;
  if (placementCoordinate(entity, "X") === null || placementCoordinate(entity, "Y") === null) return null;
  return entity;
}

export interface ViewportState {
  cameraPosition: [number, number, number];
  cameraTarget: [number, number, number];
  zoom: number;
  geometryLoaded: Record<string, boolean>;
  activeTool: ToolMode;
  moveStatus: MoveStatus | null;
}

interface ViewportContextValue extends ViewportState {
  setCamera: (position: [number, number, number], target: [number, number, number]) => void;
  setZoom: (zoom: number) => void;
  markGeometryLoaded: (guid: string, loaded: boolean) => void;
  setActiveTool: (tool: ToolMode) => void;
  setMoveStatus: (status: MoveStatus | null) => void;
}

const DEFAULT_STATE: ViewportState = {
  cameraPosition: [8000, -8000, 6000],
  cameraTarget: [0, 0, 0],
  zoom: 1,
  geometryLoaded: {},
  activeTool: "select",
  moveStatus: null,
};

const ViewportContext = createContext<ViewportContextValue | undefined>(undefined);

export function ViewportProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ViewportState>(DEFAULT_STATE);

  const setCamera = useCallback(
    (position: [number, number, number], target: [number, number, number]) => {
      setState((prev) => ({ ...prev, cameraPosition: position, cameraTarget: target }));
    },
    []
  );

  const setZoom = useCallback((zoom: number) => {
    setState((prev) => ({ ...prev, zoom }));
  }, []);

  const markGeometryLoaded = useCallback((guid: string, loaded: boolean) => {
    setState((prev) => ({
      ...prev,
      geometryLoaded: { ...prev.geometryLoaded, [guid]: loaded },
    }));
  }, []);

  const setActiveTool = useCallback((activeTool: ToolMode) => {
    setState((prev) => (prev.activeTool === activeTool ? prev : { ...prev, activeTool, moveStatus: null }));
  }, []);

  const setMoveStatus = useCallback((moveStatus: MoveStatus | null) => {
    setState((prev) => ({ ...prev, moveStatus }));
  }, []);

  const value = useMemo<ViewportContextValue>(
    () => ({ ...state, setCamera, setZoom, markGeometryLoaded, setActiveTool, setMoveStatus }),
    [state, setCamera, setZoom, markGeometryLoaded, setActiveTool, setMoveStatus]
  );

  return <ViewportContext.Provider value={value}>{children}</ViewportContext.Provider>;
}

export function useViewportContext(): ViewportContextValue {
  const ctx = useContext(ViewportContext);
  if (!ctx) throw new Error("useViewportContext must be used within a ViewportProvider");
  return ctx;
}
