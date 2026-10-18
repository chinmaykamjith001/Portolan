// A Chart link (spec §13.2): a gentle curve between shape boundaries with an arrowhead and its label at
// the midpoint. Double-click opens the edge popover (§13.7).

import { EdgeLabelRenderer, useInternalNode, useReactFlow, type Edge, type EdgeProps } from "@xyflow/react";
import { memo, type MouseEvent } from "react";

import type { Grounding } from "../api/types";
import { useUI } from "../state/store";
import styles from "./chart.module.css";
import { boundaryDistance } from "./layout";

export interface ConceptEdgeData {
  label: string;
  sourceShape: Grounding;
  sourceSize: number;
  targetShape: Grounding;
  targetSize: number;
  [key: string]: unknown;
}

export type ConceptEdgeType = Edge<ConceptEdgeData, "concept">;

interface Pt {
  x: number;
  y: number;
}

const ARROW_LEN = 8;
const ARROW_HALF = 4;

function unit(x: number, y: number): Pt {
  const d = Math.hypot(x, y) || 1;
  return { x: x / d, y: y / d };
}

/** Path, arrowhead and label point for a link between two node centers. Pure, for tests. */
export function edgeGeometry(a: Pt, b: Pt, ga: Grounding, sa: number, gb: Grounding, sb: number) {
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const u = unit(b.x - a.x, b.y - a.y);
  // Bend to the left of the direction of travel, so A→B and B→A never draw on top of each other.
  const bend = Math.min(28, len * 0.08);
  const ctrl = { x: (a.x + b.x) / 2 - u.y * bend * 2, y: (a.y + b.y) / 2 + u.x * bend * 2 };
  const d1 = unit(ctrl.x - a.x, ctrl.y - a.y);
  const d2 = unit(b.x - ctrl.x, b.y - ctrl.y);
  const ra = boundaryDistance(ga, sa, d1.x, d1.y) + 2;
  const rb = boundaryDistance(gb, sb, -d2.x, -d2.y) + 3;
  const start = { x: a.x + d1.x * ra, y: a.y + d1.y * ra };
  const tip = { x: b.x - d2.x * rb, y: b.y - d2.y * rb };
  const base = { x: tip.x - d2.x * ARROW_LEN, y: tip.y - d2.y * ARROW_LEN };
  const arrow = [
    tip,
    { x: base.x - d2.y * ARROW_HALF, y: base.y + d2.x * ARROW_HALF },
    { x: base.x + d2.y * ARROW_HALF, y: base.y - d2.x * ARROW_HALF },
  ];
  const mid = {
    x: 0.25 * start.x + 0.5 * ctrl.x + 0.25 * base.x,
    y: 0.25 * start.y + 0.5 * ctrl.y + 0.25 * base.y,
  };
  const r = (n: number) => Math.round(n * 10) / 10;
  return {
    path: `M${r(start.x)},${r(start.y)} Q${r(ctrl.x)},${r(ctrl.y)} ${r(base.x)},${r(base.y)}`,
    arrow: arrow.map((p) => `${r(p.x)},${r(p.y)}`).join(" "),
    mid,
  };
}

function ConceptEdgeImpl({ id, source, target, data }: EdgeProps<ConceptEdgeType>) {
  const s = useInternalNode(source);
  const t = useInternalNode(target);
  const active = useUI((st) => st.selectedId !== null && (st.selectedId === source || st.selectedId === target));
  const openEdge = useUI((st) => st.openEdge);
  const { flowToScreenPosition } = useReactFlow();
  if (!s || !t || !data) return null;

  const a = {
    x: s.internals.positionAbsolute.x + data.sourceSize / 2,
    y: s.internals.positionAbsolute.y + data.sourceSize / 2,
  };
  const b = {
    x: t.internals.positionAbsolute.x + data.targetSize / 2,
    y: t.internals.positionAbsolute.y + data.targetSize / 2,
  };
  const g = edgeGeometry(a, b, data.sourceShape, data.sourceSize, data.targetShape, data.targetSize);
  const open = (e: MouseEvent) => {
    e.stopPropagation();
    const screen = flowToScreenPosition(g.mid);
    openEdge({ linkId: id, x: screen.x, y: screen.y, flowX: g.mid.x, flowY: g.mid.y });
  };
  const cls = active ? `${styles.edge} ${styles.edgeActive}` : styles.edge;

  return (
    <>
      <g className={cls} data-active={active || undefined}>
        <path d={g.path} className={styles.edgePath} />
        <polygon points={g.arrow} className={styles.edgeArrow} />
        <path d={g.path} className={styles.edgeHit} onDoubleClick={open} />
      </g>
      {data.label && (
        <EdgeLabelRenderer>
          <div
            className={`${styles.edgeLabel} ${active ? styles.edgeLabelActive : ""} nodrag nopan`}
            style={{ transform: `translate(-50%, -50%) translate(${g.mid.x}px, ${g.mid.y}px)` }}
            onDoubleClick={open}
            title="Double-click to see why these are linked"
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const ConceptEdge = memo(ConceptEdgeImpl);
