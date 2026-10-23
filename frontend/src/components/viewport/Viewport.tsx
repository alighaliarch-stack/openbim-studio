/**
 * components/viewport/Viewport.tsx
 *
 * M06 Geometry Manager — React host for the imperative SceneManager.
 *
 * Bridges React state (model entities, selection) to the imperative
 * Three.js world: mounts SceneManager on a <canvas>, loads meshes when
 * the model changes, and forwards mouse gestures into the M04
 * SelectionContext and into SceneManager's camera controls.
 *
 * Mouse scheme (matches SceneManager's OrbitControls mouseButtons config):
 *   left click         -> select (replace, or toggle/append with a
 *                          Ctrl/Cmd/Shift modifier — see resolveSelectAction)
 *   left drag          -> nothing yet (reserved for a future selection
 *                          rectangle; OrbitControls ignores left entirely,
 *                          see SceneManager.mount())
 *   middle drag        -> pan (handled entirely inside OrbitControls)
 *   right drag         -> orbit (handled entirely inside OrbitControls)
 *   double-click        -> select (replace) + focus camera on that object
 *
 * Selection state lives in SelectionContext (React state), not a
 * separate observable store, so this component just reads `guids` /
 * `primary` from useSelectionContext() and re-renders — and re-running
 * the highlight effect below — whenever they change. No manual
 * subscribe/unsubscribe plumbing is needed here.
 *
 * Day-52C Move tool: with Move active and exactly one eligible wall
 * selected (moveTarget), SceneManager shows an X/Y translation gizmo on
 * it. A left-drag on the gizmo is not a selection click. A finished
 * single-axis drag is saved as OpenBIM_Placement.<axis> through the same
 * useModel().editProperty -> POST /api/properties path as the Properties
 * panel, so the backend regenerates the STL at the new position (or
 * rejects the edit atomically and the old mesh is reloaded). The outcome
 * is reported in the toolbar via ViewportContext.moveStatus.
 */

import { useEffect, useRef } from "react";

import { MeshLoader } from "@/components/viewport/MeshLoader";
import { SceneManager } from "@/components/viewport/SceneManager";
import { useModelContext } from "@/context/ModelContext";
import { useSelectionContext } from "@/context/SelectionContext";
import {
  PLACEMENT_PSET, moveTarget, placementCoordinate, useViewportContext, type MoveAxis,
} from "@/context/ViewportContext";
import { useModel } from "@/hooks/useModel";
import { useSelection } from "@/hooks/useSelection";
import { resolveSelectAction, type ClickModifiers } from "@/selection/SelectionManager";

/** Max pointer movement (px) between down/up still counted as a click, not a drag. */
const CLICK_DRAG_THRESHOLD_PX = 5;

const MOVE_HINT = "Drag the red (X) or green (Y) arrow to move the wall.";
const MOVE_PLANE_REJECTED = "Move one axis at a time: drag the red X or green Y arrow, not the square.";

/** Convert a mouse/pointer event's canvas-relative position to NDC coords for raycasting. */
function toNdc(event: { clientX: number; clientY: number }, canvas: HTMLCanvasElement): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
    y: -((event.clientY - rect.top) / rect.height) * 2 + 1,
  };
}

export function Viewport() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneManagerRef = useRef<SceneManager | null>(null);
  const pointerDownPosRef = useRef<{ x: number; y: number } | null>(null);

  const { entities, isLoading } = useModelContext();
  const { guids, select, deselect, clear, isSelected } = useSelectionContext();
  const { selectedEntities } = useSelection();
  const { activeTool, setMoveStatus } = useViewportContext();
  const { editProperty, isSaving, saveError } = useModel();

  // Day-52C: latest values for the gizmo callbacks, which outlive renders.
  const entitiesRef = useRef(entities);
  entitiesRef.current = entities;
  const editPropertyRef = useRef(editProperty);
  editPropertyRef.current = editProperty;
  /** The move being saved, so its outcome can be reported once it settles. */
  const pendingMoveRef = useRef<{ axis: MoveAxis; value: number } | null>(null);

  const moveGuid = activeTool === "move" ? moveTarget(selectedEntities)?.guid ?? null : null;
  // No gizmo while a move is saving: the mesh is about to be replaced.
  const gizmoGuid = isSaving ? null : moveGuid;

  // Mount / unmount the Three.js scene exactly once.
  useEffect(() => {
    if (!canvasRef.current) return;

    const sceneManager = new SceneManager();
    sceneManager.mount(canvasRef.current);
    sceneManagerRef.current = sceneManager;

    const resizeObserver = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      sceneManager.resize(width, height);
    });
    if (containerRef.current) resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      sceneManager.unmount();
      sceneManagerRef.current = null;
    };
  }, []);

  // Reload meshes whenever the entity set changes. Highlighting is
  // intentionally NOT reapplied here — the effect below handles it, and
  // since it also depends on `entities`, it reruns right after this one
  // on the same render, so freshly loaded meshes never stay unhighlighted.
  useEffect(() => {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager || isLoading) return;

    const loader = new MeshLoader(sceneManager);
    loader.loadEntities(Object.values(entities));
  }, [entities, isLoading]);

  // Keep mesh highlighting in sync with SelectionContext. Reruns whenever
  // either the entity set or the selection changes, covering both "new
  // meshes need the current selection's highlight" and "selection
  // changed, existing meshes need re-highlighting" in one place.
  useEffect(() => {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager) return;

    for (const guid of Object.keys(entities)) {
      sceneManager.highlight(guid, isSelected(guid) ? "selected" : "none");
    }
  }, [entities, guids, isSelected]);

  // Day-52C: attach/detach the Move gizmo. Saving reuses the Properties
  // panel's edit path (optimistic patch, atomic backend edit, revert +
  // refetch on failure).
  useEffect(() => {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager) return;
    if (!gizmoGuid) {
      sceneManager.setMoveTarget(null);
      return;
    }
    sceneManager.setMoveTarget(gizmoGuid, {
      onCommit: (axis, deltaMm) => {
        const entity = entitiesRef.current[gizmoGuid];
        const current = entity ? placementCoordinate(entity, axis) : null;
        if (current === null) return;
        const value = current + deltaMm;
        pendingMoveRef.current = { axis, value };
        setMoveStatus({ kind: "info", text: `Saving move: ${axis} ${deltaMm > 0 ? "+" : ""}${deltaMm} mm…` });
        void editPropertyRef.current(gizmoGuid, PLACEMENT_PSET, axis, value);
      },
      onRejected: () => setMoveStatus({ kind: "error", text: MOVE_PLANE_REJECTED }),
    });
  }, [gizmoGuid, setMoveStatus]);

  // Day-52C: hint when Move becomes usable for a wall.
  useEffect(() => {
    if (moveGuid) setMoveStatus({ kind: "info", text: MOVE_HINT });
  }, [moveGuid, setMoveStatus]);

  // Day-52C: report a saved or rejected move once its request settles.
  useEffect(() => {
    const pending = pendingMoveRef.current;
    if (isSaving || !pending) return;
    pendingMoveRef.current = null;
    setMoveStatus(saveError
      ? { kind: "error", text: `Move not saved: ${saveError}` }
      : { kind: "info", text: `Moved: ${pending.axis} = ${pending.value} mm.` });
  }, [isSaving, saveError, setMoveStatus]);

  /**
   * Raycast at `event`'s position and resolve it into a selection action
   * via the shared resolveSelectAction helper (reused from
   * selection/SelectionManager.ts, same logic the Model Tree uses) so
   * plain clicks replace the selection and Ctrl/Cmd/Shift-clicks
   * toggle/append, consistently across both the tree and the viewport.
   */
  function pickAndResolveSelection(event: { clientX: number; clientY: number } & ClickModifiers): void {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager || !canvasRef.current) return;

    const ndc = toNdc(event, canvasRef.current);
    const guid = sceneManager.pick(ndc.x, ndc.y);
    const action = resolveSelectAction(guid, event);

    switch (action.kind) {
      case "clear":
        clear();
        break;
      case "replace":
        select(action.guid, false);
        break;
      case "toggle":
        if (isSelected(action.guid)) {
          deselect(action.guid);
        } else {
          select(action.guid, true);
        }
        break;
    }
  }

  function handlePointerDown(event: React.PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return; // only track the left button for click-vs-drag
    // Day-52C: the gizmo's own (native) listener runs first; a press on it
    // starts a move drag, which must not end as a selection click.
    if (sceneManagerRef.current?.isMoveDragging()) return;
    pointerDownPosRef.current = { x: event.clientX, y: event.clientY };
  }

  function handlePointerUp(event: React.PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return;
    const downPos = pointerDownPosRef.current;
    pointerDownPosRef.current = null;
    if (!downPos) return;

    const dx = event.clientX - downPos.x;
    const dy = event.clientY - downPos.y;
    const movedPx = Math.hypot(dx, dy);

    // Only treat this as a selection click if the pointer barely moved.
    // A left-drag beyond the threshold does nothing (reserved for a
    // future selection rectangle) -- specifically, it must NOT select.
    if (movedPx <= CLICK_DRAG_THRESHOLD_PX) {
      pickAndResolveSelection(event);
    }
  }

  function handleDoubleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager || !canvasRef.current) return;

    const ndc = toNdc(event, canvasRef.current);
    const guid = sceneManager.pick(ndc.x, ndc.y);
    if (guid === null) return;

    // Double-click always replaces the selection with the focused object,
    // regardless of any modifier keys held.
    select(guid, false);
    sceneManager.focusOn(guid);
  }

  return (
    <div ref={containerRef} className="viewport">
      <canvas
        ref={canvasRef}
        className="viewport__canvas"
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onDoubleClick={handleDoubleClick}
        // Right mouse now drives orbit (see SceneManager.mount()); the
        // browser's native context menu would otherwise interrupt every
        // right-drag, so it's suppressed on this canvas only.
        onContextMenu={(event) => event.preventDefault()}
      />
      {isLoading && <div className="viewport__overlay">Loading geometry…</div>}
    </div>
  );
}