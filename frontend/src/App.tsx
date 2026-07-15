/**
 * App.tsx
 *
 * Root component. Wires the M10/M04/M06 context providers and composes
 * the M01 AppShell with M02, M06, M05, and M09 panels, per the
 * "ARCHITECTURE AT A GLANCE" diagram in MVP_Summary_and_Overview.md:
 *
 *   ┌──────────────┬──────────────┬────────────────────┐
 *   │ M02: Tree    │ M06: Viewport│ M05: Properties     │
 *   │ (IFC nodes)  │ (Three.js)   │ (forms) + M09: AI   │
 *   └──────────────┴──────────────┴────────────────────┘
 */

import { AppShell } from "@/components/layout/AppShell";
import { ModelTree } from "@/components/modeltree/ModelTree";
import { PropertyPanel } from "@/components/properties/PropertyPanel";
import { Viewport } from "@/components/viewport/Viewport";
import { AIChatPanel } from "@/components/aichat/AIChatPanel";
import { ModelProvider } from "@/context/ModelContext";
import { SelectionProvider } from "@/context/SelectionContext";
import { ViewportProvider } from "@/context/ViewportContext";

export default function App() {
  return (
    <ModelProvider>
      <SelectionProvider>
        <ViewportProvider>
          <AppShell
            leftPanel={<ModelTree />}
            viewport={<Viewport />}
            rightPanel={
              <div className="right-panel">
                <PropertyPanel />
                <AIChatPanel />
              </div>
            }
          />
        </ViewportProvider>
      </SelectionProvider>
    </ModelProvider>
  );
}
