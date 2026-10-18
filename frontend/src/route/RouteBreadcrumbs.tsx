// Floating breadcrumbs + Back, top-left of the Route canvas: `Route › Method › Attention`.

import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "../design/primitives";
import { useUI } from "../state/store";
import { MAIN, stopTitle } from "./data";
import styles from "./route.module.css";

export function RouteBreadcrumbs({ paperId, status }: { paperId: string; status: ReactNode }) {
  const qc = useQueryClient();
  const routePath = useUI((s) => s.routePath);
  const routeBack = useUI((s) => s.routeBack);
  const routeJumpTo = useUI((s) => s.routeJumpTo);
  const crumbs = ["Route", ...routePath.map((id, i) => stopTitle(qc, paperId, i === 0 ? MAIN : (routePath[i - 1] ?? MAIN), id))];

  return (
    <nav className={styles.crumbs} aria-label="Where you are on the route">
      <Button quiet onClick={routeBack} disabled={routePath.length === 0} aria-label="Back one level">
        Back
      </Button>
      <ol>
        {crumbs.map((title, depth) => {
          const last = depth === crumbs.length - 1;
          return (
            <li key={depth}>
              {depth > 0 && (
                <span className={styles.crumbSep} aria-hidden="true">
                  ›
                </span>
              )}
              {last ? (
                <span aria-current="location" className={styles.crumbCurrent}>
                  {title}
                </span>
              ) : (
                <button type="button" className={styles.textButton} onClick={() => routeJumpTo(depth)}>
                  {title}
                </button>
              )}
            </li>
          );
        })}
      </ol>
      {status && (
        <span className={styles.crumbStatus} aria-live="polite">
          {status}
        </span>
      )}
    </nav>
  );
}
