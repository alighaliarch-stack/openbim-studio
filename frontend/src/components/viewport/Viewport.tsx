/**
 * components/viewport/Viewport.tsx
 *
 * M06 Geometry Manager — React host for the imperative SceneManager.
 *
 * Bridges React state (model entities, selection) to the imperative
 * Three.js world: mounts SceneManager on a <canvas>, loads meshes when
 * the model changes, syncs highlight state with M04 selection, and
 * forwards click picks back into M04 (SDS dependency: "M06 depends on
 * M07, M10"; MVP simplifies M07 out, so picks go straight to selection).
 */

import { useEffect, useRef } from "react";

import { MeshLoader } from "@/components/viewport/MeshLoader";
import { SceneManager } from "@/components/viewport/SceneManager";
import { useModelContext } from "@/context/ModelContext";
import { useSelectionContext } from "@/context/SelectionContext";

export function Viewport() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneManagerRef = useRef<SceneManager | null>(null);

  const { entities, isLoading } = useModelContext();
  const { guids: selectedGuids, select, clear } = useSelectionContext();

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

  // Reload meshes whenever the entity set changes.
  useEffect(() => {
    if (!sceneManagerRef.current || isLoading) return;
    const loader = new MeshLoader(sceneManagerRef.current);
    loader.loadEntities(Object.values(entities));
  }, [entities, isLoading]);

  // Sync highlight state with the current selection.
  useEffect(() => {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager) return;
    for (const guid of Object.keys(entities)) {
      sceneManager.highlight(guid, selectedGuids.has(guid) ? "selected" : "none");
    }
  }, [entities, selectedGuids]);

  function handleClick(event: React.MouseEvent<HTMLCanvasElement>) {
    const sceneManager = sceneManagerRef.current;
    if (!sceneManager || !canvasRef.current) return;

    const rect = canvasRef.current.getBoundingClientRect();
    const ndcX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const ndcY = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    const guid = sceneManager.pick(ndcX, ndcY);
    if (guid === null) {
      clear();
      return;
    }
    select(guid, event.ctrlKey || event.metaKey || event.shiftKey);
  }

  return (
    <div ref={containerRef} className="viewport">
      <canvas ref={canvasRef} className="viewport__canvas" onClick={handleClick} />
      {isLoading && <div className="viewport__overlay">Loading geometry…</div>}
    </div>
  );
}
