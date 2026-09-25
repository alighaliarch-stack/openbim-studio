/**
 * components/properties/PropertyPanel.tsx
 *
 * M05 Property Manager (SDS Section 3, M05).
 *
 * Reads the current selection (M04 via useSelection) and displays all
 * editable properties for the primary selected entity. Editing a
 * property calls POST /api/properties/{guid} via useModel, then applies
 * the backend-confirmed entity so the viewport and Model Tree both
 * reflect the authoritative state (driven by ModelContext.lastModified,
 * which Viewport and ModelTree already subscribe to).
 *
 * Per-field saving and success indicators are passed down to InputField
 * so the user gets immediate feedback without the whole panel going stale.
 *
 * Changes from stub version:
 *   1. Passes savingField / successField / fieldKey down to PropertyGrid
 *      so InputField can show per-row "…" / "✓" without disabling the grid.
 *   2. Clears saveError automatically when the user starts a new edit.
 *   3. Multi-select branch kept — no editing (deferred to Phase 2).
 *   4. Day-23: the grid IS now disabled while a save is in flight (the
 *      per-field "…" still marks which field is saving), and the global
 *      saving message says geometry is regenerating when it is.
 */

import { useCallback } from "react";

import { PropertyGrid } from "@/components/properties/PropertyGrid";
import { useModel } from "@/hooks/useModel";
import { useSelection } from "@/hooks/useSelection";

export function PropertyPanel() {
  const { primaryEntity, isMultiSelect, selectedEntities } = useSelection();
  const {
    editProperty, isSaving, savingField, successField, saveError,
    geometryWarningField, geometryWarning,
  } = useModel();

  // Stable wrapper so PropertyGrid/InputField don't get new refs on every render.
  const handleEdit = useCallback(
    (pset: string, prop: string, value: unknown) => {
      if (!primaryEntity) return;
      void editProperty(primaryEntity.guid, pset, prop, value);
    },
    [editProperty, primaryEntity]
  );

  // ── Empty / multi-select states ───────────────────────────────────────────

  if (selectedEntities.length === 0) {
    return (
      <div className="property-panel property-panel--empty">
        <p>No entity selected.</p>
        <p className="property-panel__hint">Click an entity in the viewport or model tree.</p>
      </div>
    );
  }

  if (isMultiSelect) {
    return (
      <div className="property-panel property-panel--multi">
        <p>{selectedEntities.length} entities selected.</p>
        <p className="property-panel__hint">
          Select a single entity to edit its properties.
        </p>
        {/* TODO(Phase 2): compute and batch-edit shared PSets. */}
      </div>
    );
  }

  if (!primaryEntity) {
    return (
      <div className="property-panel property-panel--empty">
        <p>No entity selected.</p>
      </div>
    );
  }

  // ── Single-entity view ────────────────────────────────────────────────────

  return (
    <div className="property-panel">

      {/* Entity header */}
      <header className="property-panel__header">
        <h2 className="property-panel__name" title={primaryEntity.guid}>
          {primaryEntity.name}
        </h2>
        <span className="property-panel__type">{primaryEntity.type}</span>
        <span className="property-panel__guid" title="IFC GUID">
          {primaryEntity.guid.slice(0, 8)}…
        </span>
      </header>

      {/* Layer + material summary */}
      {(primaryEntity.layer || primaryEntity.materials.length > 0) && (
        <div className="property-panel__meta">
          {primaryEntity.layer && (
            <span className="property-panel__layer">{primaryEntity.layer}</span>
          )}
          {primaryEntity.materials.map((m) => (
            <span key={m} className="property-panel__material">{m}</span>
          ))}
        </div>
      )}

      {/* Save error banner */}
      {saveError && (
        <div className="property-panel__error" role="alert">
          <strong>Save failed:</strong> {saveError}
        </div>
      )}

      {/* Property grid — per-field saving/success passed down. Day-23: the
          whole grid is locked while a save is in flight so a second edit
          can't overlap the first (the backend regenerates geometry for
          one edit at a time anyway). */}
      <PropertyGrid
        entity={primaryEntity}
        disabled={isSaving}
        savingField={savingField}
        successField={successField}
        geometryWarningField={geometryWarningField}
        geometryWarning={geometryWarning}
        onEdit={handleEdit}
      />

      {/* Global saving indicator (fallback for screen readers / no-JS).
          Day-23: an edit on an entity with geometry also regenerates that
          geometry server-side within the same request, so say so. */}
      {isSaving && (
        <div className="property-panel__saving" aria-live="polite">
          {primaryEntity.geometry ? "Saving and regenerating geometry…" : "Saving…"}
        </div>
      )}
    </div>
  );
}
