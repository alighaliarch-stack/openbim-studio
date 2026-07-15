/**
 * components/properties/PropertyPanel.tsx
 *
 * M05 Property Manager (SDS Section 3, M05).
 *
 * Reads the current selection (M04) and displays/edits the primary
 * entity's property sets, dispatching edits to M10 via useModel()'s
 * editProperty (POST /api/properties/{guid}).
 *
 * Per SRS Use Case 4 (Multi-Select and Batch Inspect): when multiple
 * entities are selected, shows a summary rather than an editable grid.
 */

import { PropertyGrid } from "@/components/properties/PropertyGrid";
import { useModel } from "@/hooks/useModel";
import { useSelection } from "@/hooks/useSelection";

export function PropertyPanel() {
  const { primaryEntity, isMultiSelect, selectedEntities } = useSelection();
  const { editProperty, isSaving, saveError } = useModel();

  if (selectedEntities.length === 0) {
    return <div className="property-panel property-panel--empty">No entity selected.</div>;
  }

  if (isMultiSelect) {
    return (
      <div className="property-panel property-panel--multi">
        <p>Multiple entities selected ({selectedEntities.length})</p>
        {/* FR: "Property panel shows common properties only (or disabled
            edit for now)". TODO(Phase 2): compute and edit shared PSets
            across the whole selection. */}
      </div>
    );
  }

  if (!primaryEntity) {
    return <div className="property-panel property-panel--empty">No entity selected.</div>;
  }

  return (
    <div className="property-panel">
      <header className="property-panel__header">
        <h2>{primaryEntity.name}</h2>
        <span className="property-panel__type">{primaryEntity.type}</span>
      </header>

      <PropertyGrid
        entity={primaryEntity}
        disabled={isSaving}
        onEdit={(pset, prop, value) => void editProperty(primaryEntity.guid, pset, prop, value)}
      />

      {saveError && <p className="property-panel__error">Failed to save: {saveError}</p>}
    </div>
  );
}
