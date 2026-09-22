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
 *
 * Day-20: NumberField no longer treats an emptied field as 0 (Number("")
 * === 0 in JS was previously sending a silent zero-edit) or lets a value
 * at/below an optional `exclusiveMin` reach onChange — both are rejected
 * locally with a visible message and no network request. Separately,
 * `warningMessage` renders a server-confirmed geometry-regeneration
 * warning for this specific field (PropertyEditResponse.geometry_warning,
 * threaded down via PropertyGrid) — distinct from the local validation
 * message above: this one means the edit DID save, but geometry didn't.
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
  /** Numeric fields only: reject values <= this (exclusive lower bound). */
  exclusiveMin?: number;
  /** Server-confirmed geometry-regeneration warning for this field, or null. */
  warningMessage?: string | null;
}

export function InputField({
  label,
  value,
  disabled,
  onChange,
  isSaving = false,
  isSuccess = false,
  exclusiveMin,
  warningMessage = null,
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
            exclusiveMin={exclusiveMin}
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
        {warningMessage && (
          <p className="input-field__geometry-warning" role="status">
            ⚠ {warningMessage}
          </p>
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
  exclusiveMin,
}: {
  value: number;
  disabled: boolean;
  onChange: (v: unknown) => void;
  exclusiveMin?: number;
}) {
  const [local, setLocal] = useState(String(value));
  const [validationError, setValidationError] = useState<string | null>(null);

  // Sync when entity is replaced (e.g. after a confirmed save).
  useEffect(() => {
    setLocal(String(value));
    setValidationError(null);
  }, [value]);

  function commit() {
    const trimmed = local.trim();

    // Day-20 fix: Number("") === 0 in JS, so an emptied field used to be
    // sent as a silent zero-edit. Reject it locally instead -- no
    // request, revert to the last confirmed value, explain why.
    if (trimmed === "") {
      setValidationError("Value is required.");
      setLocal(String(value));
      return;
    }

    const parsed = Number(trimmed);
    if (isNaN(parsed)) {
      setValidationError("Enter a valid number.");
      setLocal(String(value));
      return;
    }

    if (exclusiveMin !== undefined && parsed <= exclusiveMin) {
      setValidationError(
        exclusiveMin === 0
          ? "Value must be greater than zero."
          : `Value must be greater than ${exclusiveMin}.`
      );
      setLocal(String(value));
      return;
    }

    setValidationError(null);
    if (parsed !== value) {
      onChange(parsed);
    }
  }

  return (
    <>
      <input
        type="number"
        className="input-field__number"
        value={local}
        disabled={disabled}
        onChange={(e) => {
          setLocal(e.target.value);
          if (validationError) setValidationError(null);
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setLocal(String(value));
            setValidationError(null);
          }
        }}
      />
      {validationError && (
        <p className="input-field__validation-error" role="alert">
          {validationError}
        </p>
      )}
    </>
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
