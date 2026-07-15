/**
 * context/SelectionContext.tsx
 *
 * M04 Selection Manager (SDS Section 3, M04).
 *
 * Owns SelectionState {guids: Set<guid>, primary: guid, lastModified},
 * synced across M02 Model Tree and M06 Geometry Manager via this shared
 * context, per the "Communication Patterns" contract in SDS 2.2.
 *
 * TODO(Phase 2): Migrate to Zustand (per SDS Appendix repo structure,
 * apps/web/src/stores/) once selection needs to be observed outside the
 * React tree (e.g. by a non-React Three.js render loop).
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface SelectionContextValue {
  guids: Set<string>;
  primary: string | null;
  /** M04 API: select(guid, append?) */
  select: (guid: string, append?: boolean) => void;
  /** M04 API: deselect(guid) */
  deselect: (guid: string) => void;
  /** M04 API: clear() */
  clear: () => void;
  isSelected: (guid: string) => boolean;
}

const SelectionContext = createContext<SelectionContextValue | undefined>(undefined);

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [guids, setGuids] = useState<Set<string>>(new Set());
  const [primary, setPrimary] = useState<string | null>(null);

  const select = useCallback((guid: string, append = false) => {
    setGuids((prev) => {
      const next = append ? new Set(prev) : new Set<string>();
      next.add(guid);
      return next;
    });
    setPrimary(guid);
  }, []);

  const deselect = useCallback((guid: string) => {
    setGuids((prev) => {
      const next = new Set(prev);
      next.delete(guid);
      return next;
    });
    setPrimary((prevPrimary) => (prevPrimary === guid ? null : prevPrimary));
  }, []);

  const clear = useCallback(() => {
    setGuids(new Set());
    setPrimary(null);
  }, []);

  const isSelected = useCallback((guid: string) => guids.has(guid), [guids]);

  const value = useMemo<SelectionContextValue>(
    () => ({ guids, primary, select, deselect, clear, isSelected }),
    [guids, primary, select, deselect, clear, isSelected]
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelectionContext(): SelectionContextValue {
  const ctx = useContext(SelectionContext);
  if (!ctx) throw new Error("useSelectionContext must be used within a SelectionProvider");
  return ctx;
}
