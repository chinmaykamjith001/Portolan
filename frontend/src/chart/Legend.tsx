// Collapsible legend, bottom-right (spec §13.3). Same shapes as the nodes, at glyph size.

import { useState } from "react";

import type { Grounding } from "../api/types";
import { GROUNDING_LABEL, GroundingGlyph } from "../design/GroundingGlyph";
import styles from "./chart.module.css";

const ORDER: Grounding[] = ["concept_only", "in_code", "in_library", "shown_by_running", "unknown"];

export function Legend() {
  const [open, setOpen] = useState(true);
  return (
    <section className={styles.legend} aria-label="Legend">
      <button
        type="button"
        className={styles.legendToggle}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "Hide legend" : "Legend"}
      </button>
      {open && (
        <ul>
          {ORDER.map((g) => (
            <li key={g}>
              <span className={styles.legendIcon}>
                <GroundingGlyph grounding={g} size={14} />
              </span>
              {GROUNDING_LABEL[g]}
            </li>
          ))}
          <li>
            <span className={styles.legendIcon} aria-hidden="true">
              <span className={styles.legendMismatch}>!</span>
            </span>
            Mismatch with the code
          </li>
          <li>
            <span className={`${styles.legendIcon} ${styles.legendMono}`} aria-hidden="true">
              ↘ 4
            </span>
            Has a map inside (double-click)
          </li>
          <li>
            <span className={styles.legendIcon} aria-hidden="true">
              <span className={styles.legendDot} />
            </span>
            First principle
          </li>
        </ul>
      )}
    </section>
  );
}
