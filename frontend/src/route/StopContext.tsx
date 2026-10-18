// What the drawer shows for a Route stop (spec §13.5, §13.6): the stop it is on, and "What the paper
// says here" above the concept's tabs.

import { useQueryClient } from "@tanstack/react-query";

import { useRoute } from "../api/hooks";
import type { Route, Stop } from "../api/types";
import { anchorLabel } from "../detail/anchors";
import { Chip } from "../design/primitives";
import { useUI } from "../state/store";
import { MAIN, stopTitle } from "./data";
import styles from "./route.module.css";

export interface RouteStop {
  stop: Stop;
  route: Route;
  index: number; // 0-based
  total: number;
  /** Make another stop of this route current (and keep the drawer open). */
  goTo: (stopId: string) => void;
}

/** The current Route stop, or null outside the Route view / when it isn't in the route on screen. */
export function useRouteStop(): RouteStop | null {
  const view = useUI((s) => s.view);
  const paperId = useUI((s) => s.paperId);
  const stopId = useUI((s) => s.stopId);
  const routeId = useUI((s) => s.routePath[s.routePath.length - 1] ?? MAIN);
  const selectStop = useUI((s) => s.selectStop);
  const route = useRoute(view === "route" ? paperId : null, routeId).data ?? null;
  if (view !== "route" || !route || !stopId) return null;
  const index = route.stops.findIndex((s) => s.id === stopId);
  const stop = route.stops[index];
  if (!stop) return null;
  return {
    stop,
    route,
    index,
    total: route.stops.length,
    goTo: (id) => {
      const target = route.stops.find((s) => s.id === id);
      if (target) selectStop(target, true);
    },
  };
}

function Link({ ctx, stopId }: { ctx: RouteStop; stopId: string }) {
  const qc = useQueryClient();
  const paperId = useUI((s) => s.paperId) ?? "";
  const n = ctx.route.stops.findIndex((s) => s.id === stopId) + 1;
  if (n === 0) return null;
  return (
    <button type="button" className={styles.textButton} onClick={() => ctx.goTo(stopId)}>
      stop {n}
      <span className={styles.srOnly}>, {stopTitle(qc, paperId, ctx.route.id, stopId)}</span>
    </button>
  );
}

export function StopSummary({ ctx }: { ctx: RouteStop }) {
  const { stop } = ctx;
  const seen = stop.seen_again_at.filter((id) => ctx.route.stops.some((s) => s.id === id));
  const first = stop.first_stop_id && ctx.route.stops.some((s) => s.id === stop.first_stop_id);
  return (
    <section className={styles.summary} aria-label="What the paper says here">
      <h3 className={styles.summaryHead}>What the paper says here</h3>
      <p className={styles.summaryText}>{stop.summary || "This part of the paper has no summary yet."}</p>
      <div className={styles.summaryMeta}>
        <Chip title={stop.anchor_id}>{stop.section_label}</Chip>
        {stop.side_roads.map((id) => (
          <Chip key={id} title={id}>
            {anchorLabel(id)}
          </Chip>
        ))}
      </div>
      {first && stop.first_stop_id && (
        <p className={styles.recur}>
          First mentioned at <Link ctx={ctx} stopId={stop.first_stop_id} />.
        </p>
      )}
      {seen.length > 0 && (
        <p className={styles.recur}>
          Seen again at{" "}
          {seen.map((id, i) => (
            <span key={id}>
              {i > 0 && (i === seen.length - 1 ? " and " : ", ")}
              <Link ctx={ctx} stopId={id} />
            </span>
          ))}
          .
        </p>
      )}
    </section>
  );
}
