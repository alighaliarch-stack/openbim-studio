/**
 * components/modeltree/ModelTree.tsx
 *
 * M02 Model Tree Manager (SDS Section 3, M02).
 *
 * Renders the IFC spatial hierarchy (Project -> Site -> Building ->
 * Storey -> Element) from M10 (via ModelContext) and drives selection
 * through M04 (via SelectionContext), per the dependency contract in
 * SDS 8.1: "M02 depends on M04, M10".
 *
 * TODO(Phase 2): search/filter input, virtualized rendering for large
 * trees, and M03 Layer Tree visibility filtering.
 */

import { useModelContext } from "@/context/ModelContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { TreeNode } from "@/components/modeltree/TreeNode";

export function ModelTree() {
  const { tree, isLoading, error } = useModelContext();
  const { select, clear } = useSelectionContext();

  if (isLoading) {
    return <div className="model-tree model-tree--loading">Loading model…</div>;
  }

  if (error) {
    return <div className="model-tree model-tree--error">Failed to load model: {error}</div>;
  }

  return (
    <nav className="model-tree" aria-label="Model spatial hierarchy">
      {tree.length === 0 ? (
        <p className="model-tree__empty">No entities loaded.</p>
      ) : (
        <ul>
          {tree.map((node) => (
            <TreeNode
              key={node.guid}
              node={node}
              depth={0}
              onSelect={(guid, modifiers) =>
                modifiers.ctrlKey || modifiers.metaKey || modifiers.shiftKey
                  ? select(guid, true)
                  : select(guid, false)
              }
            />
          ))}
        </ul>
      )}
      {/* Click on empty space below the tree clears selection (SRS Use Case 4). */}
      <div className="model-tree__blank-area" onClick={() => clear()} />
    </nav>
  );
}
