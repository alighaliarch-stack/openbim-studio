/**
 * components/layout/Toolbar.tsx
 *
 * M01 GUI Layer — Toolbar (tool mode switcher: select, move, rotate,
 * draw, etc., per SDS Section 3, M01).
 *
 * MVP implements "select" (FR-042/043 raycasting) and, since Day-52C,
 * "move": single-axis X/Y translation of one selected wall (see
 * Viewport.tsx). Rotate and draw are still stubbed as disabled buttons.
 *
 * Day-52C: the active tool lives in ViewportContext so the Viewport can
 * act on it. Move is enabled only while exactly one eligible wall is
 * selected (moveTarget); losing that selection returns to Select. The
 * Move status message (hint, rejected drag, failed save) is shown here.
 */

import { useEffect } from "react";

import { moveTarget, useViewportContext, type ToolMode } from "@/context/ViewportContext";
import { useSelection } from "@/hooks/useSelection";

const TOOLS: { mode: ToolMode; label: string; enabledInMvp: boolean }[] = [
  { mode: "select", label: "Select", enabledInMvp: true },
  { mode: "move", label: "Move", enabledInMvp: true },
  { mode: "rotate", label: "Rotate", enabledInMvp: false },
  { mode: "draw", label: "Draw", enabledInMvp: false },
];

export function Toolbar() {
  const { activeTool, setActiveTool, moveStatus } = useViewportContext();
  const { selectedEntities } = useSelection();
  const canMove = moveTarget(selectedEntities) !== null;

  useEffect(() => {
    if (activeTool === "move" && !canMove) setActiveTool("select");
  }, [activeTool, canMove, setActiveTool]);

  function titleFor(mode: ToolMode, enabledInMvp: boolean, label: string): string {
    if (!enabledInMvp) return `${label} (Phase 2)`;
    if (mode === "move" && !canMove) return "Move (select one wall first)";
    return label;
  }

  return (
    <header className="toolbar">
      <span className="toolbar__title">OpenBIM Studio</span>
      <div className="toolbar__tools">
        {TOOLS.map((tool) => (
          <button
            key={tool.mode}
            type="button"
            data-tool={tool.mode}
            disabled={!tool.enabledInMvp || (tool.mode === "move" && !canMove)}
            aria-pressed={activeTool === tool.mode}
            title={titleFor(tool.mode, tool.enabledInMvp, tool.label)}
            onClick={() => setActiveTool(tool.mode)}
          >
            {tool.label}
          </button>
        ))}
      </div>
      {activeTool === "move" && moveStatus && (
        <span
          className={`toolbar__status toolbar__status--${moveStatus.kind}`}
          role={moveStatus.kind === "error" ? "alert" : "status"}
        >
          {moveStatus.text}
        </span>
      )}
    </header>
  );
}
