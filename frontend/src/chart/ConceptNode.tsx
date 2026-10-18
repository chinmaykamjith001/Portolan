// A Chart node (spec §13.3): shape encodes grounding, color is reserved for state.

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";

import type { Concept, Grounding } from "../api/types";
import { GROUNDING_LABEL } from "../design/GroundingGlyph";
import { Shimmer } from "../design/primitives";
import { useUI } from "../state/store";
import styles from "./chart.module.css";

export interface ConceptNodeData {
  concept: Concept;
  center: boolean;
  size: number;
  /** Concepts inside its layer, when that layer is already loaded. */
  childCount: number | null;
  /** Findings inside its sub-map (M8); 0 until findings exist. */
  issuesInside: number;
  /** ms delay for the staggered fade-in after a jump, or null for no animation. */
  enterDelay: number | null;
  [key: string]: unknown;
}

export type ConceptNodeType = Node<ConceptNodeData, "concept">;

/** Can the user jump into this concept from the current layer? */
export function canJumpInto(c: Concept, isCenter: boolean): boolean {
  return !isCenter && !c.is_first_principle;
}

export function ShapeOutline({
  grounding,
  size,
  className,
  inset = 1,
}: {
  grounding: Grounding;
  size: number;
  className?: string;
  inset?: number;
}) {
  const c = size / 2;
  const r = size / 2 - inset;
  switch (grounding) {
    case "in_code":
      return (
        <rect
          className={className}
          x={inset}
          y={inset}
          width={size - 2 * inset}
          height={size - 2 * inset}
          rx={size * 0.18}
        />
      );
    case "in_library": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i + Math.PI / 6;
        return `${c + r * Math.cos(a)},${c + r * Math.sin(a)}`;
      }).join(" ");
      return <polygon className={className} points={pts} />;
    }
    case "shown_by_running":
      return <circle className={className} cx={c} cy={c} r={r} strokeDasharray="7 5" />;
    case "unknown":
      return <circle className={className} cx={c} cy={c} r={r} strokeDasharray="0.5 5" strokeLinecap="round" />;
    default:
      return <circle className={className} cx={c} cy={c} r={r} />;
  }
}

export function describeConcept(c: Concept, center: boolean, jumpable: boolean): string {
  const parts = [c.title, GROUNDING_LABEL[c.grounding]];
  if (center) parts.push("center of this map");
  if (c.issue_count_self > 0) parts.push("mismatch with the code");
  if (c.is_first_principle) parts.push("first principle");
  else if (jumpable) parts.push(c.has_layer ? "can jump in" : "not mapped yet");
  return parts.join(", ");
}

function ConceptNodeImpl({ id, data }: NodeProps<ConceptNodeType>) {
  const selected = useUI((s) => s.selectedId === id);
  const { concept: c, center, size, childCount, issuesInside, enterDelay } = data;
  const jumpable = canJumpInto(c, center);
  const mismatch = c.issue_count_self > 0;
  const gistPending = c.gen_state === "generating" || !c.gist;
  const state = [selected ? "selected" : "", center ? "center" : "", mismatch ? "mismatch" : ""]
    .filter(Boolean)
    .join(" ");
  const cls = [
    styles.node,
    styles[`shape_${c.grounding}`] ?? "",
    center ? styles.center : "",
    selected ? styles.selected : "",
    mismatch ? styles.mismatch : "",
    enterDelay !== null ? styles.enter : "",
  ].join(" ");
  // "Terra incognita" (DECISIONS §20 #3): a subtle hover hint on concepts whose layer isn't drawn yet.
  const hint = jumpable && !c.has_layer ? "Terra incognita: not mapped yet" : undefined;

  return (
    <div
      className={cls}
      style={{ width: size, height: size, animationDelay: enterDelay !== null ? `${enterDelay}ms` : undefined }}
      data-grounding={c.grounding}
      data-state={state || "default"}
      title={hint}
    >
      <Handle type="target" position={Position.Top} className={styles.handle} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} className={styles.handle} isConnectable={false} />
      <svg className={styles.shape} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <ShapeOutline grounding={c.grounding} size={size} className={styles.ring} inset={-3} />
        <ShapeOutline grounding={c.grounding} size={size} className={styles.outline} />
      </svg>
      <div className={styles.body}>
        <span className={styles.label}>{c.title}</span>
        <span className={styles.gist}>{gistPending ? <Shimmer width="80%" /> : c.gist}</span>
      </div>
      {mismatch && (
        <span className={styles.mismatchGlyph} aria-hidden="true">
          !
        </span>
      )}
      {issuesInside > 0 && (
        <span className={styles.badge} aria-label={`${issuesInside} issues inside`}>
          {issuesInside}
        </span>
      )}
      {jumpable && c.has_layer && (
        <span className={styles.jump} aria-hidden="true">
          ↘{childCount !== null ? ` ${childCount}` : ""}
        </span>
      )}
      {c.is_first_principle && <span className={styles.leaf} aria-hidden="true" />}
    </div>
  );
}

export const ConceptNode = memo(ConceptNodeImpl);
