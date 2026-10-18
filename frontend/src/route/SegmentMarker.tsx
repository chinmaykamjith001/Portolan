// "§3 Model architecture": sits in the free space beside the first stop of each section (spec §13.5).

import type { Node, NodeProps } from "@xyflow/react";
import { memo } from "react";

import styles from "./route.module.css";

export interface MarkerData {
  text: string;
  side: "left" | "right";
  [key: string]: unknown;
}

export type MarkerNodeType = Node<MarkerData, "marker">;

function SegmentMarkerImpl({ data }: NodeProps<MarkerNodeType>) {
  return (
    <div className={`${styles.marker} ${data.side === "left" ? styles.markerLeft : ""}`}>
      <span>{data.text}</span>
    </div>
  );
}

export const SegmentMarker = memo(SegmentMarkerImpl);
