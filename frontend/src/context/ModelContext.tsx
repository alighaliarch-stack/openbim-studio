/**
 * context/ModelContext.tsx
 *
 * Frontend mirror of the M10 IFC Semantic Layer's entity/tree state.
 *
 * Changes from previous version:
 *   - Added applyConfirmedEntity(guid, entity) — replaces the full
 *     entity in local state with the backend-confirmed version returned
 *     by POST /api/properties/{guid}. Called by useModel after a
 *     successful save so server-side normalisation is reflected without
 *     a full refetch.
 */

import {
  createContext, useCallback, useContext, useEffect,
  useMemo, useState, type ReactNode,
} from "react";

import { ApiClient } from "@/api/client";
import type { IfcEntity, SpatialNode } from "@/types/entities";

interface ModelState {
  entities: Record<string, IfcEntity>;
  tree: SpatialNode[];
  lastModified: Date;
  isLoading: boolean;
  error: string | null;
}

interface ModelContextValue extends ModelState {
  refetch: () => Promise<void>;
  /** Optimistically patch a single property in local state (M05 write path). */
  applyPropertyEdit: (guid: string, pset: string, prop: string, value: unknown) => void;
  /**
   * Replace the full local entity with the backend-confirmed version
   * returned by POST /api/properties/{guid}. Ensures server-side
   * normalisation is reflected immediately without a full refetch.
   */
  applyConfirmedEntity: (guid: string, entity: IfcEntity) => void;
}

const ModelContext = createContext<ModelContextValue | undefined>(undefined);

const apiClient = new ApiClient();

export function ModelProvider({ children }: { children: ReactNode }) {
  const [entities, setEntities] = useState<Record<string, IfcEntity>>({});
  const [tree, setTree]         = useState<SpatialNode[]>([]);
  const [lastModified, setLastModified] = useState(new Date());
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError]         = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [modelResponse, treeResponse] = await Promise.all([
        apiClient.getModel(),
        apiClient.getTree(),
      ]);
      const entityMap = Object.fromEntries(
        modelResponse.entities.map((e) => [e.guid, e])
      );
      setEntities(entityMap);
      setTree(treeResponse.nodes);
      setLastModified(new Date());
    } catch (err) {
      console.error("Failed to load model:", err);
      setError(err instanceof Error ? err.message : "Unknown error loading model");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { void refetch(); }, [refetch]);

  // Optimistic patch — called before the network request.
  const applyPropertyEdit = useCallback(
    (guid: string, pset: string, prop: string, value: unknown) => {
      setEntities((prev) => {
        const entity = prev[guid];
        if (!entity) return prev;
        return {
          ...prev,
          [guid]: {
            ...entity,
            psets: { ...entity.psets, [pset]: { ...entity.psets[pset], [prop]: value } },
          },
        };
      });
      setLastModified(new Date());
    },
    []
  );

  // Confirmed update — replaces the entity with the backend response.
  const applyConfirmedEntity = useCallback(
    (guid: string, entity: IfcEntity) => {
      setEntities((prev) => ({ ...prev, [guid]: entity }));
      setLastModified(new Date());
    },
    []
  );

  const value = useMemo<ModelContextValue>(
    () => ({
      entities, tree, lastModified, isLoading, error,
      refetch, applyPropertyEdit, applyConfirmedEntity,
    }),
    [entities, tree, lastModified, isLoading, error,
     refetch, applyPropertyEdit, applyConfirmedEntity]
  );

  return <ModelContext.Provider value={value}>{children}</ModelContext.Provider>;
}

export function useModelContext(): ModelContextValue {
  const ctx = useContext(ModelContext);
  if (!ctx) throw new Error("useModelContext must be used within a ModelProvider");
  return ctx;
}

export { apiClient };
