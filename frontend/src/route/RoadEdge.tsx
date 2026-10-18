// A connector between two stops (spec §13.5): a curve with an arrowhead and the transition written
// along it. The geometry comes from layout.ts; connectors already traveled are drawn in the accent.

import { EdgeLabelRenderer, type Edge, type EdgeProps } from "@xyflow/react";
import { memo } from "react";

import styles from "./route.module.css";

export interface RoadEdgeData {
  path: string;
  arrow: string;
  mid: { x: number; y: number };
  label: string;
  traveled: boolean;
  [key: string]: unknown;
}

export type RoadEdgeType = Edge<RoadEdgeData, "road">;

function RoadEdgeImpl({ data }: EdgeProps<RoadEdgeType>) {
  if (!data) return null;
  return (
    <>
      <g className={data.traveled ? `${styles.road} ${styles.roadTraveled}` : styles.road} data-traveled={data.traveled || undefined}>
        <path d={data.path} className={styles.roadPath} />
        <polygon points={data.arrow} className={styles.roadArrow} />
      </g>
      {data.label && (
        <EdgeLabelRenderer>
          <div
            className={`${styles.roadLabel} ${data.traveled ? styles.roadLabelTraveled : ""} nodrag nopan`}
            style={{ transform: `translate(-50%, -50%) translate(${data.mid.x}px, ${data.mid.y}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const RoadEdge = memo(RoadEdgeImpl);
