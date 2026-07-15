/**
 * hooks/useSelection.ts
 *
 * Convenience hook combining M04 SelectionContext with M10 ModelContext
 * to resolve the currently-selected entity/entities, used by M05
 * Property Manager and M06 Geometry Manager.
 */

import { useMemo } from "react";

import { useModelContext } from "@/context/ModelContext";
import { useSelectionContext } from "@/context/SelectionContext";
import type { IfcEntity } from "@/types/entities";

export interface UseSelectionResult {
  selectedGuids: string[];
  primaryGuid: string | null;
  primaryEntity: IfcEntity | null;
  selectedEntities: IfcEntity[];
  isMultiSelect: boolean;
}

export function useSelection(): UseSelectionResult {
  const { guids, primary } = useSelectionContext();
  const { entities } = useModelContext();

  return useMemo(() => {
    const selectedGuids = Array.from(guids);
    const selectedEntities = selectedGuids
      .map((guid) => entities[guid])
      .filter((e): e is IfcEntity => e !== undefined);

    return {
      selectedGuids,
      primaryGuid: primary,
      primaryEntity: primary ? entities[primary] ?? null : null,
      selectedEntities,
      isMultiSelect: selectedGuids.length > 1,
    };
  }, [guids, primary, entities]);
}
