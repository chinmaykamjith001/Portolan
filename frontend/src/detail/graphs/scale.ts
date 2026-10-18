// Axis maths and number formatting for the in-house SVG graphs.

export interface Range {
  lo: number;
  hi: number;
  ticks: number[];
}

/** A "nice" axis range that includes zero, with about `count` tick marks. */
export function niceRange(min: number, max: number, count = 4): Range {
  const lo0 = Math.min(0, min);
  const hi0 = Math.max(0, max);
  if (hi0 === lo0) return { lo: 0, hi: 1, ticks: [0, 1] };
  const raw = (hi0 - lo0) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const lo = Math.floor(lo0 / step) * step;
  const hi = Math.ceil(hi0 / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
  return { lo, hi, ticks };
}

/** 3 significant digits, no trailing zeros: 0.6590 → "0.659", 1200 → "1200". */
export function fmt(n: number): string {
  if (Number.isInteger(n) && Math.abs(n) < 1e6) return String(n);
  const s = Math.abs(n) >= 1e6 || (Math.abs(n) < 1e-3 && n !== 0) ? n.toExponential(2) : n.toPrecision(3);
  return s.includes("e") ? s : String(Number(s));
}

/** Shorten a label to fit `width` px at 12 px mono (≈7 px a character). */
export function fit(label: string, width: number): string {
  const max = Math.max(1, Math.floor(width / 7.2));
  if (label.length <= max) return label;
  return max <= 1 ? label.slice(0, 1) : `${label.slice(0, max - 1)}…`;
}

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** About `count` round tick values inside [min, max] (unlike niceRange, zero is not forced in). */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}
