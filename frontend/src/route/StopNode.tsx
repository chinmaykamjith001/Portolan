// A Route stop (spec §13.5): number, section label, title, grounding glyph, mismatch marker, and the
// "Jump in · N" affordance when it opens a sub-route.

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, type MouseEvent } from "react";

import type { Concept, Stop } from "../api/types";
import { anchorLabel } from "../detail/anchors";
import { GROUNDING_LABEL, GroundingGlyph } from "../design/GroundingGlyph";
import { useUI } from "../state/store";
import styles from "./route.module.css";

export interface StopNodeData {
  stop: Stop;
  number: number; // 1-based position along the route
  concept: Concept | null;
  /** Stops inside its sub-route, when that is already loaded. */
  subCount: number | null;
  /** "First mentioned at stop 3" / "Seen again at stop 7, 9", or null. */
  recurrence: string | null;
  /** ms delay for the staggered fade-in after a jump, or null for no animation. */
  enterDelay: number | null;
  [key: string]: unknown;
}

export type StopNodeType = Node<StopNodeData, "stop">;

export function describeStop(d: StopNodeData): string {
  const parts = [`Stop ${d.number}`, d.stop.section_label, d.stop.title];
  if (d.concept) parts.push(GROUNDING_LABEL[d.concept.grounding]);
  if ((d.concept?.issue_count_self ?? 0) > 0) parts.push("mismatch with the code");
  if (d.stop.sub_route_id) parts.push("can jump in");
  return parts.join(", ");
}

function StopNodeImpl({ id, data }: NodeProps<StopNodeType>) {
  const current = useUI((s) => s.stopId === id);
  const { stop, number, concept, subCount, recurrence, enterDelay } = data;
  const mismatch = (concept?.issue_count_self ?? 0) > 0;
  const cls = [
    styles.stop,
    current ? styles.current : "",
    mismatch ? styles.mismatch : "",
    enterDelay !== null ? styles.enter : "",
  ].join(" ");
  const jump = (e: MouseEvent) => {
    e.stopPropagation();
    useUI.getState().routeJumpIn(stop.id);
  };
  const roads = stop.side_roads.map(anchorLabel);

  return (
    <div
      className={cls}
      style={{ animationDelay: enterDelay !== null ? `${enterDelay}ms` : undefined }}
      data-state={[current ? "current" : "", mismatch ? "mismatch" : ""].filter(Boolean).join(" ") || "default"}
    >
      <Handle id="top" type="target" position={Position.Top} className={styles.handle} isConnectable={false} />
      <Handle id="right" type="source" position={Position.Right} className={styles.handle} isConnectable={false} />
      <Handle id="bottom" type="source" position={Position.Bottom} className={styles.handle} isConnectable={false} />
      <div className={styles.head}>
        <span className={styles.num}>{number}</span>
        <span className={styles.section}>{stop.section_label}</span>
        <span className={styles.marks}>
          {mismatch && (
            <span className={styles.mismatchGlyph} aria-hidden="true">
              !
            </span>
          )}
          {concept && (
            <span className={styles.glyph}>
              <GroundingGlyph grounding={concept.grounding} />
            </span>
          )}
        </span>
      </div>
      <div className={styles.title}>{stop.title}</div>
      <div className={styles.foot}>
        {recurrence && <span className={styles.recurrence}>{recurrence}</span>}
        {stop.sub_route_id && (
          <button type="button" className={`${styles.jump} nodrag nopan`} onClick={jump}>
            Jump in{subCount !== null ? ` · ${subCount}` : ""}
          </button>
        )}
      </div>
      {roads.length > 0 && (
        <div className={styles.sideRoad} aria-label={`Side road: ${roads.join(", ")}`}>
          <span className={styles.sideStub} aria-hidden="true" />
          <span className={styles.sideLabel}>{roads.join(", ")}</span>
        </div>
      )}
    </div>
  );
}

export const StopNode = memo(StopNodeImpl);
