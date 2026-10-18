// Floating breadcrumbs + Back, top-left of the canvas (spec §13.2): `Transformer › Self-attention › Softmax`.

import { useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "../design/primitives";
import { useUI } from "../state/store";
import styles from "./chart.module.css";
import { conceptTitle } from "./data";

export function Breadcrumbs({ paperId, status }: { paperId: string; status: ReactNode }) {
  const qc = useQueryClient();
  const path = useUI((s) => s.path);
  const back = useUI((s) => s.back);
  const jumpTo = useUI((s) => s.jumpTo);
  const crumbs = Array.from({ length: path.length + 1 }, (_, depth) => conceptTitle(qc, paperId, path, depth));

  return (
    <nav className={styles.crumbs} aria-label="Where you are on the map">
      <Button quiet onClick={back} disabled={path.length === 0} aria-label="Back one level">
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
                <button type="button" className={styles.textButton} onClick={() => jumpTo(depth)}>
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
