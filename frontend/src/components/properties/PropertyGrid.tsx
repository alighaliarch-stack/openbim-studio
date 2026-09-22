/**
 * components/properties/PropertyGrid.tsx
 *
 * M05 Property Manager — PropertyGrid + PSetAccordion.
 *
 * Changes from previous version:
 *   - Accepts savingField / successField (format: "pset::prop") from
 *     PropertyPanel and threads them down to InputField so each row
 *     shows its own saving/success indicator rather than disabling the
 *     entire grid.
 *   - `disabled` prop kept but now only used if the caller explicitly
 *     needs to lock the whole grid (e.g. during a refetch revert).
 */

import { useState } from "react";

import { InputField } from "@/components/properties/InputField";
import type { IfcEntity, PropertySet } from "@/types/entities";

/**
 * Day-20 client-side bound for the three wall dimensional properties.
 *
 * Sourced from the backend: WallGenerator._positive_number() (services/
 * geometry/wall_generator.py) treats any value <= 0 as invalid and
 * silently skips regeneration rather than erroring — that's the actual
 * rule that determines success/failure on this edit path, so the client
 * mirrors it exactly (exclusive minimum of 0, i.e. value must be > 0).
 * (ComponentRegistry's wall ParamField also declares `minimum=0` for
 * these three params, but that bound is inclusive and isn't enforced
 * against this edit path at all — it only gates POST /api/scad/render's
 * direct params — so it's not the authoritative source here.)
 */
const WALL_DIMENSION_PSET = "Qto_WallBaseQuantities";
const WALL_DIMENSION_PROPS = new Set(["Length", "Height", "Thickness"]);

function wallDimensionExclusiveMin(psetName: string, prop: string): number | undefined {
  return psetName === WALL_DIMENSION_PSET && WALL_DIMENSION_PROPS.has(prop) ? 0 : undefined;
}

interface PropertyGridProps {
  entity: IfcEntity;
  disabled: boolean;
  /** "pset::prop" key currently saving, or null. */
  savingField?: string | null;
  /** "pset::prop" key whose last save succeeded (auto-cleared by parent). */
  successField?: string | null;
  /** "pset::prop" key whose last save persisted but didn't regenerate geometry, or null. */
  geometryWarningField?: string | null;
  /** Message to show for geometryWarningField, or null. */
  geometryWarning?: string | null;
  onEdit: (pset: string, prop: string, value: unknown) => void;
}

export function PropertyGrid({
  entity,
  disabled,
  savingField = null,
  successField = null,
  geometryWarningField = null,
  geometryWarning = null,
  onEdit,
}: PropertyGridProps) {
  const psetNames = Object.keys(entity.psets);

  if (psetNames.length === 0) {
    return <p className="property-grid__empty">No property sets on this entity.</p>;
  }

  return (
    <div className="property-grid">
      {psetNames.map((psetName) => (
        <PSetAccordion
          key={psetName}
          psetName={psetName}
          pset={entity.psets[psetName]}
          disabled={disabled}
          savingField={savingField}
          successField={successField}
          geometryWarningField={geometryWarningField}
          geometryWarning={geometryWarning}
          onEdit={(prop, value) => onEdit(psetName, prop, value)}
        />
      ))}
    </div>
  );
}

interface PSetAccordionProps {
  psetName: string;
  pset: PropertySet;
  disabled: boolean;
  savingField: string | null;
  successField: string | null;
  geometryWarningField: string | null;
  geometryWarning: string | null;
  onEdit: (prop: string, value: unknown) => void;
}

function PSetAccordion({
  psetName, pset, disabled, savingField, successField,
  geometryWarningField, geometryWarning, onEdit,
}: PSetAccordionProps) {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <section className="pset-accordion">
      <button
        type="button"
        className="pset-accordion__header"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
      >
        <span className="pset-accordion__chevron">{isOpen ? "▾" : "▸"}</span>
        <span className="pset-accordion__name">{psetName}</span>
        <span className="pset-accordion__count">{Object.keys(pset).length} props</span>
      </button>

      {isOpen && (
        <div className="pset-accordion__body">
          {Object.entries(pset).map(([prop, value]) => {
            const key = `${psetName}::${prop}`;
            return (
              <InputField
                key={prop}
                label={prop}
                value={value}
                disabled={disabled}
                isSaving={savingField === key}
                isSuccess={successField === key}
                warningMessage={geometryWarningField === key ? geometryWarning : null}
                exclusiveMin={wallDimensionExclusiveMin(psetName, prop)}
                onChange={(newValue) => onEdit(prop, newValue)}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}
