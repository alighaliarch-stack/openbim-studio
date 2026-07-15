/**
 * components/modeltree/TreeNode.tsx
 *
 * Recursive node renderer for M02 Model Tree. Handles expand/collapse and
 * emits selection events up to ModelTree, which routes them through M04.
 */

import { useState } from "react";

import { useSelectionContext } from "@/context/SelectionContext";
import type { ClickModifiers } from "@/selection/SelectionManager";
import type { SpatialNode } from "@/types/entities";

interface TreeNodeProps {
  node: SpatialNode;
  depth: number;
  onSelect: (guid: string, modifiers: ClickModifiers) => void;
}

export function TreeNode({ node, depth, onSelect }: TreeNodeProps) {
  const [isExpanded, setIsExpanded] = useState(node.is_expanded);
  const { isSelected } = useSelectionContext();
  const hasChildren = node.children.length > 0;
  const selected = isSelected(node.guid);

  return (
    <li>
      <div
        className={`model-tree__row${selected ? " model-tree__row--selected" : ""}`}
        style={{ paddingLeft: `${depth * 16}px` }}
        onClick={(event) => {
          event.stopPropagation();
          onSelect(node.guid, {
            shiftKey: event.shiftKey,
            ctrlKey: event.ctrlKey,
            metaKey: event.metaKey,
          });
        }}
      >
        {hasChildren && (
          <button
            type="button"
            className="model-tree__toggle"
            aria-label={isExpanded ? "Collapse" : "Expand"}
            onClick={(event) => {
              event.stopPropagation();
              setIsExpanded((prev) => !prev);
            }}
          >
            {isExpanded ? "▾" : "▸"}
          </button>
        )}
        <span className="model-tree__type">{node.type}</span>
        <span className="model-tree__name">{node.name}</span>
      </div>

      {hasChildren && isExpanded && (
        <ul>
          {node.children.map((child) => (
            <TreeNode key={child.guid} node={child} depth={depth + 1} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  );
}
