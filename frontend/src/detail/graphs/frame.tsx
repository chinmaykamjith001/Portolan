// Shared geometry for the plots: a 360 × 236 viewBox that scales with the drawer width.
// Room for the axis titles is always reserved, so a title arriving never moves the plot.

import type { ReactNode } from "react";

import type { Colors } from "./data";
import styles from "./graphs.module.css";
import { fit, fmt, niceTicks, type Range } from "./scale";

export const W = 360;
export const H = 236;
export const M = { l: 44, r: 10, t: 22, b: 46 };
export const PLOT = { w: W - M.l - M.r, h: H - M.t - M.b };

type Color = Colors[number];
const CYCLE: Color[] = ["accent", "ink2", "ink3"];

/** Series 0-2 use accent, ink-2, ink-3; further series repeat them, faded. */
export function seriesColor(colors: Colors | undefined, j: number): Color {
  return colors?.[j] ?? CYCLE[j % 3] ?? "accent";
}
export const fadeOf = (colors: Colors | undefined, j: number): number => (colors?.[j] === undefined && j >= 3 ? 0.55 : 1);

export const fillClass = (c: Color) => styles[c === "ink2" ? "fillInk2" : c === "ink3" ? "fillInk3" : "fillAccent"];
export const strokeClass = (c: Color) => styles[c === "ink2" ? "strokeInk2" : c === "ink3" ? "strokeInk3" : "strokeAccent"];
export const swatchClass = (c: Color) => styles[c === "ink2" ? "swatchInk2" : c === "ink3" ? "swatchInk3" : "swatchAccent"];

export const yPos = (range: Range, v: number) => M.t + PLOT.h * (1 - (v - range.lo) / (range.hi - range.lo));

export function Axes({ range }: { range: Range }) {
  return (
    <g>
      {range.ticks.map((t) => (
        <g key={t}>
          <line className={styles.grid} x1={M.l} x2={W - M.r} y1={yPos(range, t)} y2={yPos(range, t)} />
          <text className={styles.tick} x={M.l - 6} y={yPos(range, t) + 4} textAnchor="end">
            {fmt(t)}
          </text>
        </g>
      ))}
      <line className={styles.axis} x1={M.l} x2={M.l} y1={M.t} y2={M.t + PLOT.h} />
      <line className={styles.axis} x1={M.l} x2={W - M.r} y1={yPos(range, 0)} y2={yPos(range, 0)} />
    </g>
  );
}

const TICK_Y = M.t + PLOT.h + 16;

/** Category labels along the bottom; thins them out when the slots get narrow. */
export function XLabels({ x }: { x: string[] }) {
  const slot = PLOT.w / x.length;
  const skip = Math.max(1, Math.ceil(36 / slot));
  return (
    <g>
      {x.map((label, i) =>
        i % skip === 0 ? (
          <text key={i} className={styles.tick} x={M.l + slot * (i + 0.5)} y={TICK_Y} textAnchor="middle">
            <title>{label}</title>
            {fit(label, slot * skip - 4)}
          </text>
        ) : null,
      )}
    </g>
  );
}

/** Tick labels for a numeric x axis spanning [lo, hi] across the plot, padded by `pad` px. */
export function NumericXLabels({ lo, hi, pad }: { lo: number; hi: number; pad: number }) {
  const at = (v: number) => M.l + pad + ((v - lo) / (hi - lo || 1)) * (PLOT.w - 2 * pad);
  return (
    <g>
      {niceTicks(lo, hi, 4).map((t) => (
        <g key={t}>
          <line className={styles.axis} x1={at(t)} x2={at(t)} y1={M.t + PLOT.h} y2={M.t + PLOT.h + 3} />
          <text className={styles.tick} x={at(t)} y={TICK_Y} textAnchor="middle">
            {fmt(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

/** Axis titles: x under the tick labels, y above the axis (horizontal, so it stays readable). */
export function AxisTitles({ xLabel, yLabel, left = M.l }: { xLabel: string; yLabel: string; left?: number }) {
  return (
    <g>
      {yLabel && (
        <text className={styles.axisTitle} x={left} y={12}>
          {yLabel}
        </text>
      )}
      {xLabel && (
        <text className={styles.axisTitle} x={M.l + PLOT.w / 2} y={H - 6} textAnchor="middle">
          {xLabel}
        </text>
      )}
    </g>
  );
}

export function Legend({ names, colors }: { names: string[]; colors: Colors | undefined }) {
  if (names.length < 2 || names.some((n) => n === "")) return null;
  return (
    <ul className={styles.legend} aria-label="Legend">
      {names.map((n, i) => (
        <li key={i}>
          <span
            className={`${styles.swatch} ${swatchClass(seriesColor(colors, i))}`}
            style={{ opacity: fadeOf(colors, i) }}
            aria-hidden="true"
          />
          {n}
        </li>
      ))}
    </ul>
  );
}

export function Plot({ label, children }: { label: string; children: ReactNode }) {
  return (
    <svg className={styles.svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {children}
    </svg>
  );
}
