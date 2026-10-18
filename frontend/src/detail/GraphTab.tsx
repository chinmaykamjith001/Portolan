// Graph tab (spec §13.6): the SVG chart for a concept, computed by the sandbox.

import { ApiError } from "../api/client";
import { useGraph } from "../api/hooks";
import type { GraphResult, GraphSpec } from "../api/types";
import { Button, Shimmer } from "../design/primitives";
import styles from "./detail.module.css";
import { canRender, GraphView } from "./graphs";

/** The spec when a graph exists and can be drawn; `null` for "none" or malformed data. */
export function drawableGraph(result: GraphResult | null | undefined): GraphSpec | null {
  if (!result || !("concept_id" in result)) return null;
  return canRender(result) ? result : null;
}

export function GraphTab({ paperId, conceptId }: { paperId: string; conceptId: string }) {
  const q = useGraph(paperId, conceptId);
  const spec = drawableGraph(q.data);
  if (q.error) {
    return (
      <div className={styles.error} role="alert">
        <p>{q.error instanceof ApiError ? q.error.message : "Couldn't load this graph."}</p>
        <Button onClick={() => void q.refetch()}>Try again</Button>
      </div>
    );
  }
  if (!q.data) {
    return (
      <div className={styles.graphArea} aria-busy="true" aria-hidden="true">
        <Shimmer width="40%" />
        <div className={styles.graphSkeleton} />
      </div>
    );
  }
  return (
    <div className={styles.graphArea} aria-live="polite">
      {spec ? <GraphView spec={spec} /> : <p className={styles.note}>This graph can&apos;t be shown.</p>}
    </div>
  );
}
