/**
 * selection/SelectionManager.ts
 *
 * M04 Selection Manager — MultiSelectHandler logic (SDS Section 3, M04
 * "Internal Components").
 *
 * Pure, framework-free helper for interpreting a click event's modifier
 * keys into a selection action. Kept separate from SelectionContext.tsx
 * so the same logic can be reused by both the Model Tree (DOM click
 * events) and the Viewport (Three.js raycaster pick + the originating
 * DOM event's modifiers).
 *
 * TODO(Phase 2): Replace the React Context-based SelectionContext with a
 * Zustand SelectionStore (per SDS "Internal Components: SelectionStore
 * (Zustand)") if selection needs to be read outside of React render
 * cycles.
 */

export type SelectAction =
  | { kind: "replace"; guid: string }
  | { kind: "toggle"; guid: string }
  | { kind: "clear" };

export interface ClickModifiers {
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}

/**
 * FR: Multi-Select and Batch Inspect.
 *   - Plain click on entity -> replace selection with that entity
 *   - Ctrl/Cmd+click on entity -> toggle that entity in the selection
 *   - Ctrl/Cmd+click on an already-selected entity -> deselect it
 *   - Click on empty space -> clear selection (guid is null)
 */
export function resolveSelectAction(guid: string | null, modifiers: ClickModifiers): SelectAction {
  if (guid === null) {
    return { kind: "clear" };
  }

  const isMultiSelectModifier = modifiers.ctrlKey || modifiers.metaKey || modifiers.shiftKey;
  return isMultiSelectModifier ? { kind: "toggle", guid } : { kind: "replace", guid };
}
