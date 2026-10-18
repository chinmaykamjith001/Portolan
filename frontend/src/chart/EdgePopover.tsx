// Edge popover (spec §13.7): anchored at the link's midpoint, follows pan/zoom, closes on Esc
// (global) or a click outside.

import { useViewport } from "@xyflow/react";
import { useCallback } from "react";

import { useEdge } from "../api/hooks";
import type { LayerPayload } from "../api/types";
import { Popover, Shimmer } from "../design/primitives";
import { useUI } from "../state/store";
import styles from "./chart.module.css";

const WIDTH = 340;

/** Strip the little Markdown the edge prompt produces; the popover shows plain paragraphs. */
export function plainParagraphs(md: string): string[] {
  return md
    .split(/\n\s*\n/)
    .map((p) =>
      p
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/\*(.+?)\*/g, "$1")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/\s+/g, " ")
        .trim(),
    )
    .filter(Boolean);
}

export function EdgePopover({
  paperId,
  payload,
  width,
  height,
}: {
  paperId: string;
  payload: LayerPayload;
  width: number;
  height: number;
}) {
  const edge = useUI((s) => s.edge);
  const closeEdge = useUI((s) => s.closeEdge);
  const select = useUI((s) => s.select);
  const { x: vx, y: vy, zoom } = useViewport();
  const link = payload.layer.links.find((l) => l.id === edge?.linkId);
  const { data, isError } = useEdge(paperId, link ? link.id : null);
  const onClose = useCallback(() => closeEdge(), [closeEdge]);
  if (!edge || !link) return null;

  const ax = edge.flowX !== undefined ? edge.flowX * zoom + vx : edge.x;
  const ay = edge.flowY !== undefined ? edge.flowY * zoom + vy : edge.y;
  const left = Math.min(Math.max(16, ax - WIDTH / 2), Math.max(16, width - WIDTH - 16));
  const top = Math.min(ay + 14, Math.max(16, height - 260));
  const titleOf = (id: string) => payload.concepts[id]?.title ?? id;

  return (
    <Popover x={left} y={top} onClose={onClose} label="Why these are linked">
      <div className={styles.edgePopover}>
        {data ? (
          <>
            <p className={styles.edgeHeadline}>{data.headline}</p>
            {plainParagraphs(data.body_markdown).map((p, i) => (
              <p key={i}>{p}</p>
            ))}
            <p className={styles.edgeWhy}>
              <span>Why it matters: </span>
              {data.why_it_matters}
            </p>
          </>
        ) : isError ? (
          <p>Couldn&apos;t explain this link. Close and try again.</p>
        ) : (
          <div aria-busy="true" aria-label="Writing the explanation">
            <Shimmer width="70%" />
            <Shimmer />
            <Shimmer />
            <Shimmer width="85%" />
          </div>
        )}
        <div className={styles.edgeLinks}>
          {[link.source, link.target].map((id) => (
            <button
              key={id}
              type="button"
              className={styles.textButton}
              onClick={() => {
                closeEdge();
                select(id);
              }}
            >
              Open {titleOf(id)}
            </button>
          ))}
        </div>
      </div>
    </Popover>
  );
}
