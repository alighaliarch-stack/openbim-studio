/**
 * components/layout/Toolbar.tsx
 *
 * M01 GUI Layer — Toolbar (tool mode switcher: select, move, rotate,
 * draw, etc., per SDS Section 3, M01).
 *
 * MVP only implements "select" (FR-042/043 raycasting); other tool modes
 * are stubbed as disabled buttons for Phase 2 (transform gizmos).
 */

import { useState } from "react";

type ToolMode = "select" | "move" | "rotate" | "draw";

const TOOLS: { mode: ToolMode; label: string; enabledInMvp: boolean }[] = [
  { mode: "select", label: "Select", enabledInMvp: true },
  { mode: "move", label: "Move", enabledInMvp: false },
  { mode: "rotate", label: "Rotate", enabledInMvp: false },
  { mode: "draw", label: "Draw", enabledInMvp: false },
];

export function Toolbar() {
  const [activeTool, setActiveTool] = useState<ToolMode>("select");

  return (
    <header className="toolbar">
      <span className="toolbar__title">OpenBIM Studio</span>
      <div className="toolbar__tools">
        {TOOLS.map((tool) => (
          <button
            key={tool.mode}
            type="button"
            disabled={!tool.enabledInMvp}
            aria-pressed={activeTool === tool.mode}
            title={tool.enabledInMvp ? tool.label : `${tool.label} (Phase 2)`}
            onClick={() => setActiveTool(tool.mode)}
          >
            {tool.label}
          </button>
        ))}
      </div>
    </header>
  );
}
