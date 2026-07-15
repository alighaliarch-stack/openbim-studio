/**
 * components/layout/AppShell.tsx
 *
 * M01 GUI Layer — AppShell (SDS Section 3, M01 "Internal Components").
 *
 * Top-level 3-column layout: Model Tree | Viewport | Property Panel,
 * matching the GUI wireframe (2-openscad_bim_gui_wireframe.html) and the
 * "ARCHITECTURE AT A GLANCE" diagram in MVP_Summary_and_Overview.md.
 */

import type { ReactNode } from "react";

import { Toolbar } from "@/components/layout/Toolbar";

interface AppShellProps {
  leftPanel: ReactNode;
  viewport: ReactNode;
  rightPanel: ReactNode;
}

export function AppShell({ leftPanel, viewport, rightPanel }: AppShellProps) {
  return (
    <div className="app-shell">
      <Toolbar />
      <div className="app-shell__body">
        <aside className="app-shell__left-panel">{leftPanel}</aside>
        <main className="app-shell__viewport">{viewport}</main>
        <aside className="app-shell__right-panel">{rightPanel}</aside>
      </div>
    </div>
  );
}
