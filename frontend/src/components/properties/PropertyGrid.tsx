/**
 * components/properties/PropertyGrid.tsx
 *
 * M05 Property Manager — PropertyGrid + PSetAccordion (SDS Section 3,
 * M05 "Internal Components").
 *
 * Renders one collapsible section per PSet (e.g. Pset_WallCommon,
 * Qto_WallBaseQuantities), each containing editable InputField rows.
 */

import { useState } from "react";

import { InputField } from "@/components/properties/InputField";
import type { IfcEntity, PropertySet } from "@/types/entities";

interface PropertyGridProps {
  entity: IfcEntity;
  disabled: boolean;
  onEdit: (pset: string, prop: string, value: unknown) => void;
}

export function PropertyGrid({ entity, disabled, onEdit }: PropertyGridProps) {
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
  onEdit: (prop: string, value: unknown) => void;
}

function PSetAccordion({ psetName, pset, disabled, onEdit }: PSetAccordionProps) {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <section className="pset-accordion">
      <button
        type="button"
        className="pset-accordion__header"
        onClick={() => setIsOpen((prev) => !prev)}
      >
        {isOpen ? "▾" : "▸"} {psetName}
      </button>
      {isOpen && (
        <div className="pset-accordion__body">
          {Object.entries(pset).map(([prop, value]) => (
            <InputField
              key={prop}
              label={prop}
              value={value}
              disabled={disabled}
              onChange={(newValue) => onEdit(prop, newValue)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
