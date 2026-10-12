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
 *   4. Day-24: on FAILURE, first reverts the edited property locally to
 *      its pre-edit value (so the revert holds even if the refetch also
 *      fails), reports an actionable message (see describeSaveFailure),
 *      and exposes `feedbackGuid` so feedback is shown only for the
 *      entity it belongs to.
 *   5. Day-28: backend edits are atomic -- a rejected edit (422 invalid
 *      value, 502 geometry not regenerated, 500 model not saved) changed
 *      nothing server-side, so the existing revert + refetch path applies
 *      unchanged; structured 5xx rejections now show the backend's message.
 */

import { useCallback, useRef, useState } from "react";

import { ApiError } from "@/api/client";
import { apiClient, useModelContext } from "@/context/ModelContext";

/** A field key is "pset::prop", used to track per-field state. */
function fieldKey(pset: string, prop: string): string {
  return `${pset}::${prop}`;
}

const UNREACHABLE_MESSAGE = "Could not reach the backend — check that the server is running, then retry.";
const SERVER_ERROR_MESSAGE = "The server could not save this change.";
const REJECTED_MESSAGE = "The server rejected this change.";

/**
 * Day-24: pull the backend's own human-readable error text out of a
 * FastAPI error body, or null. Handles the two shapes this API returns:
 *   {"detail": {"error": "...", "detail": "..."}}  (HTTPException + ErrorResponse)
 *   {"detail": [{"loc": [...], "msg": "..."}]}     (request validation, 422)
 * plus a plain {"detail": "..."} string.
 */
function backendErrorText(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;

  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: { loc?: unknown[]; msg?: unknown }) => {
        if (typeof item?.msg !== "string") return null;
        const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .filter((message): message is string => message !== null);
    return messages.length > 0 ? messages.join("; ") : null;
  }
  if (detail && typeof detail === "object" && typeof (detail as { error?: unknown }).error === "string") {
    const { error, detail: extra } = detail as { error: string; detail?: unknown };
    return typeof extra === "string" && extra ? `${error} (${extra})` : error;
  }
  return null;
}

/** Day-28: true for the backend's own ErrorResponse envelope ({"detail": {"error": "..."}}). */
export function isStructuredError(body: unknown): boolean {
  if (!body || typeof body !== "object" || !("detail" in body)) return false;
  const detail = (body as { detail: unknown }).detail;
  return Boolean(detail) && typeof detail === "object" && typeof (detail as { error?: unknown }).error === "string";
}

/**
 * Day-24: turn a failed editProperty request into a message the user can
 * act on, instead of the raw "POST /properties/<guid> failed with status N".
 *   - fetch itself threw (no HTTP response)  -> backend unreachable
 *   - 5xx with a structured ErrorResponse (Day-28: 502 geometry regeneration
 *     failed, 500 model not saved) -> the backend's own error text; the
 *     backend answered, and the edit was rejected without changing anything
 *   - other 5xx: in dev the Vite proxy also answers 500 when the backend is
 *     down, so probe GET /api/health once to tell "unreachable" from a
 *     genuine server-side failure
 *   - 4xx -> the backend's own error text, when it sent any
 */
export async function describeSaveFailure(err: unknown): Promise<string> {
  if (!(err instanceof ApiError)) return UNREACHABLE_MESSAGE;

  if (err.status >= 500 && isStructuredError(err.detail)) {
    return backendErrorText(err.detail) ?? SERVER_ERROR_MESSAGE;
  }

  if (err.status >= 500) {
    try {
      await apiClient.getHealth();
      return SERVER_ERROR_MESSAGE;
    } catch {
      return UNREACHABLE_MESSAGE;
    }
  }

  return backendErrorText(err.detail) ?? REJECTED_MESSAGE;
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
  /**
   * Day-24: guid of the entity the current saving/success/warning/error
   * feedback belongs to (the entity of the most recent edit), or null.
   * PropertyPanel shows that feedback only while this entity is selected.
   */
  feedbackGuid: string | null;
}

export function useModel(): UseModelResult {
  const { entities, applyPropertyEdit, applyConfirmedEntity, refetch } = useModelContext();

  // Day-24: latest entities, readable from inside editProperty without
  // making it depend on (and be recreated for) every entity change.
  const entitiesRef = useRef(entities);
  entitiesRef.current = entities;

  const [isSaving, setIsSaving]       = useState(false);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [successField, setSuccessField] = useState<string | null>(null);
  const [saveError, setSaveError]     = useState<string | null>(null);
  const [geometryWarningField, setGeometryWarningField] = useState<string | null>(null);
  const [geometryWarning, setGeometryWarning] = useState<string | null>(null);
  const [feedbackGuid, setFeedbackGuid] = useState<string | null>(null);

  /**
   * Day-23: id of the most recently started edit. Each editProperty call
   * takes the next id; only the latest edit may clear isSaving /
   * savingField when it settles, so an older request finishing first
   * can't hide the saving indicator of a newer one still in flight.
   */
  const latestRequestIdRef = useRef(0);

  const editProperty = useCallback(
    async (guid: string, pset: string, prop: string, value: unknown) => {
      const key = fieldKey(pset, prop);
      const requestId = ++latestRequestIdRef.current;

      setIsSaving(true);
      setSavingField(key);
      setFeedbackGuid(guid);
      setSaveError(null);
      setSuccessField(null);
      setGeometryWarningField(null);
      setGeometryWarning(null);

      // Pre-edit value, kept so a failed save can be reverted locally.
      const previousEntity = entitiesRef.current[guid];
      const previousValue = previousEntity?.psets[pset]?.[prop];

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
        console.error("Property edit failed, reverting via refetch:", err);
        // Day-24: revert the optimistic update locally first, so the UI
        // stops showing the unsaved value even if the refetch below also
        // fails (e.g. backend unreachable).
        if (previousEntity) {
          applyPropertyEdit(guid, pset, prop, previousValue);
        }
        setSaveError(await describeSaveFailure(err));
        // Full refetch still restores the authoritative state when it can.
        await refetch();
      } finally {
        if (requestId === latestRequestIdRef.current) {
          setIsSaving(false);
          setSavingField(null);
        }
      }
    },
    [applyPropertyEdit, applyConfirmedEntity, refetch]
  );

  return {
    editProperty, isSaving, savingField, successField, saveError,
    geometryWarningField, geometryWarning, feedbackGuid,
  };
}

/** Stable key builder — exported so PropertyPanel can use the same format. */
export { fieldKey };
