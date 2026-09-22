/**
 * hooks/useModel.ts
 *
 * M05 Property Manager write path: calls POST /api/properties/{guid},
 * then applies the backend-confirmed entity from the response so the UI
 * reflects the authoritative server state (not just the optimistic patch).
 *
 * Changes from the previous version:
 *   1. On SUCCESS: calls applyConfirmedEntity(guid, response.entity) so
 *      any server-side normalisation (type coercion, computed fields) is
 *      reflected immediately without a full refetch.
 *   2. Exposes `savingField: string | null` and `successField: string | null`
 *      so PropertyPanel can show per-field saving / success indicators
 *      rather than disabling the entire grid.
 *   3. On FAILURE: still reverts via refetch() as before.
 */

import { useCallback, useState } from "react";

import { apiClient, useModelContext } from "@/context/ModelContext";

/** A field key is "pset::prop", used to track per-field state. */
function fieldKey(pset: string, prop: string): string {
  return `${pset}::${prop}`;
}

interface UseModelResult {
  editProperty: (guid: string, pset: string, prop: string, value: unknown) => Promise<void>;
  /** True while any save is in-flight (kept for grid-level disabled fallback). */
  isSaving: boolean;
  /** The "pset::prop" key currently saving, or null. */
  savingField: string | null;
  /** The "pset::prop" key whose last save succeeded (cleared after 1.5 s). */
  successField: string | null;
  saveError: string | null;
  /**
   * Day-20: the "pset::prop" key whose last save persisted successfully
   * but did not regenerate geometry, or null. Distinct from saveError —
   * the property save itself did NOT fail (see PropertyEditResponse.
   * geometry_warning). Persists until the next edit starts (no auto-clear
   * timer), since it's more consequential than the transient success tick.
   */
  geometryWarningField: string | null;
  /** The message to show for geometryWarningField, or null. */
  geometryWarning: string | null;
}

export function useModel(): UseModelResult {
  const { applyPropertyEdit, applyConfirmedEntity, refetch } = useModelContext();

  const [isSaving, setIsSaving]       = useState(false);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [successField, setSuccessField] = useState<string | null>(null);
  const [saveError, setSaveError]     = useState<string | null>(null);
  const [geometryWarningField, setGeometryWarningField] = useState<string | null>(null);
  const [geometryWarning, setGeometryWarning] = useState<string | null>(null);

  const editProperty = useCallback(
    async (guid: string, pset: string, prop: string, value: unknown) => {
      const key = fieldKey(pset, prop);

      setIsSaving(true);
      setSavingField(key);
      setSaveError(null);
      setSuccessField(null);
      setGeometryWarningField(null);
      setGeometryWarning(null);

      // 1. Optimistic local update — UI responds immediately.
      applyPropertyEdit(guid, pset, prop, value);

      try {
        // 2. Confirmed update from backend response.
        const response = await apiClient.editProperty(guid, {
          pset_name: pset,
          property_name: prop,
          value,
        });

        // Apply the authoritative entity so server-side normalisation
        // (type coercion, computed fields) is reflected in the UI.
        applyConfirmedEntity(guid, response.entity);

        // Show a brief per-field success tick. The property WAS saved
        // (server-confirmed above), so this shows even if geometry_warning
        // is also set below — a geometry-regen warning must never read as
        // "the property edit failed".
        setSuccessField(key);
        setTimeout(() => setSuccessField(null), 1500);

        if (response.geometry_warning) {
          setGeometryWarningField(key);
          setGeometryWarning(response.geometry_warning);
        }
      } catch (err) {
        // Revert optimistic update: full refetch restores authoritative state.
        console.error("Property edit failed, reverting via refetch:", err);
        setSaveError(err instanceof Error ? err.message : "Failed to save property");
        await refetch();
      } finally {
        setIsSaving(false);
        setSavingField(null);
      }
    },
    [applyPropertyEdit, applyConfirmedEntity, refetch]
  );

  return {
    editProperty, isSaving, savingField, successField, saveError,
    geometryWarningField, geometryWarning,
  };
}

/** Stable key builder — exported so PropertyPanel can use the same format. */
export { fieldKey };
