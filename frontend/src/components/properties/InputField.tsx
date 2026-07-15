/**
 * components/properties/InputField.tsx
 *
 * M05 Property Manager — single editable property row.
 *
 * Detects the runtime type of `value` and renders the appropriate
 * control:
 *   boolean  → <input type="checkbox">
 *   number   → <input type="number">
 *   string   → <input type="text">
 *   other    → read-only <span> (arrays, objects, null, undefined)
 *
 * Calls `onChange` only on confirmed edits (blur for text/number,
 * immediate for checkbox) so the parent's onEdit → POST cycle is not
 * triggered on every keystroke.
 *
 * Local state tracks the in-progress text value so the input is
 * controlled without firing a save on every character.
 */

import { useEffect, useState } from "react";

interface InputFieldProps {
  label: string;
  value: unknown;
  disabled: boolean;
  onChange: (newValue: unknown) => void;
  /** Set to true while this specific field's save is in-flight. */
  isSaving?: boolean;
  /** Set to true once this field's last save succeeded. */
  isSuccess?: boolean;
}

export function InputField({
  label,
  value,
  disabled,
  onChange,
  isSaving = false,
  isSuccess = false,
}: InputFieldProps) {
  const type = detectType(value);

  return (
    <div className="input-field" data-type={type}>
      <label className="input-field__label" title={label}>
        {label}
      </label>
      <div className="input-field__control">
        {type === "boolean" && (
          <BoolField
            value={value as boolean}
            disabled={disabled || isSaving}
            onChange={onChange}
          />
        )}
        {type === "number" && (
          <NumberField
            value={value as number}
            disabled={disabled || isSaving}
            onChange={onChange}
          />
        )}
        {type === "string" && (
          <TextField
            value={value as string}
            disabled={disabled || isSaving}
            onChange={onChange}
          />
        )}
        {type === "readonly" && (
          <span className="input-field__readonly">{formatReadonly(value)}</span>
        )}
        {isSaving && <span className="input-field__status input-field__status--saving">…</span>}
        {isSuccess && !isSaving && (
          <span className="input-field__status input-field__status--success">✓</span>
        )}
      </div>
    </div>
  );
}

// ── Sub-controls ──────────────────────────────────────────────────────────────

function BoolField({
  value,
  disabled,
  onChange,
}: {
  value: boolean;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  return (
    <input
      type="checkbox"
      className="input-field__checkbox"
      checked={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

function NumberField({
  value,
  disabled,
  onChange,
}: {
  value: number;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const [local, setLocal] = useState(String(value));

  // Sync when entity is replaced (e.g. after a confirmed save).
  useEffect(() => {
    setLocal(String(value));
  }, [value]);

  function commit() {
    const parsed = Number(local);
    if (!isNaN(parsed) && parsed !== value) {
      onChange(parsed);
    } else {
      // Reset to last confirmed value if the user typed a non-number.
      setLocal(String(value));
    }
  }

  return (
    <input
      type="number"
      className="input-field__number"
      value={local}
      disabled={disabled}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setLocal(String(value));
      }}
    />
  );
}

function TextField({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const [local, setLocal] = useState(value);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  function commit() {
    if (local !== value) onChange(local);
    else setLocal(value); // reset in case no change
  }

  return (
    <input
      type="text"
      className="input-field__text"
      value={local}
      disabled={disabled}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setLocal(value);
      }}
    />
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

type FieldType = "boolean" | "number" | "string" | "readonly";

function detectType(value: unknown): FieldType {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (typeof value === "string") return "string";
  return "readonly";
}

function formatReadonly(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return `[${value.join(", ")}]`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
