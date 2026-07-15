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
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface ViewportState {
  cameraPosition: [number, number, number];
  cameraTarget: [number, number, number];
  zoom: number;
  geometryLoaded: Record<string, boolean>;
}

interface ViewportContextValue extends ViewportState {
  setCamera: (position: [number, number, number], target: [number, number, number]) => void;
  setZoom: (zoom: number) => void;
  markGeometryLoaded: (guid: string, loaded: boolean) => void;
}

const DEFAULT_STATE: ViewportState = {
  cameraPosition: [8000, -8000, 6000],
  cameraTarget: [0, 0, 0],
  zoom: 1,
  geometryLoaded: {},
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

  const value = useMemo<ViewportContextValue>(
    () => ({ ...state, setCamera, setZoom, markGeometryLoaded }),
    [state, setCamera, setZoom, markGeometryLoaded]
  );

  return <ViewportContext.Provider value={value}>{children}</ViewportContext.Provider>;
}

export function useViewportContext(): ViewportContextValue {
  const ctx = useContext(ViewportContext);
  if (!ctx) throw new Error("useViewportContext must be used within a ViewportProvider");
  return ctx;
}
