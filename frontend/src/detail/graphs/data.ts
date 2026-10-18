// Turns the sandbox's JSON into typed models, rejecting anything malformed. The renderers never
// see unchecked data, so a bad graph shows nothing instead of crashing the drawer.
//
// Shapes are defined by the backend (backend/portolan/detail/graph.py):
//   bar           {"x": [str], "series": [{"name", "y": [num]}], "x_label", "y_label"}
//   line          {"x": [num], "series": [...], ...}          numeric x, ascending
//   heatmap       {"rows": [str], "cols": [str], "values": [[num]], ...}
//   before_after  {"labels": [str], "before": [num], "after": [num], "before_name", "after_name", ...}
//   distribution  {"x": [num], "y": [num], "style": "bars" | "curve", "bin_width", ...}
//                 (raw {"values": [num], "bins": int} is binned here as well)

import type { GraphSpec } from "../../api/types";
import { isNum } from "./scale";

export interface Series {
  name: string;
  y: number[];
}
export interface SeriesData {
  /** Category labels (always present). */
  x: string[];
  /** The numeric x values, when every x is a number in ascending order. */
  xs: number[] | null;
  series: Series[];
  xLabel: string;
  yLabel: string;
}
export interface HeatmapData {
  rows: string[];
  cols: string[];
  values: number[][];
  xLabel: string;
  yLabel: string;
}

export type Colors = ("accent" | "ink2" | "ink3")[];

export type ParsedGraph =
  | { kind: "bar" | "line" | "before_after"; data: SeriesData; colors?: Colors }
  | { kind: "distribution"; data: SeriesData; style: "bars" | "curve" }
  | { kind: "heatmap"; data: HeatmapData };

const MAX_BARS = 60;
const MAX_POINTS = 200;
const MAX_SERIES = 6;
const MAX_CELLS = 24;
const MAX_SAMPLES = 5000;

function asRecord(v: unknown): Record<string, unknown> | null {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

const text = (v: unknown, max = 40): string => (typeof v === "string" ? v.trim().slice(0, max) : "");

const labels = (v: unknown, max: number): string[] | null =>
  Array.isArray(v) && v.length > 0 && v.length <= max && v.every((s) => typeof s === "string" || isNum(s))
    ? v.map((s) => String(s))
    : null;

const numbers = (v: unknown, max: number, n?: number): number[] | null =>
  Array.isArray(v) && v.length > 0 && v.length <= max && v.every(isNum) && (n === undefined || v.length === n)
    ? (v as number[])
    : null;

const axisLabels = (data: Record<string, unknown>) => ({ xLabel: text(data.x_label), yLabel: text(data.y_label) });

/** Numeric x values, if they are all numbers and ascend. */
function numericX(v: unknown): number[] | null {
  if (!Array.isArray(v) || !v.every(isNum)) return null;
  const xs = v as number[];
  return xs.every((n, i) => i === 0 || n > (xs[i - 1] ?? n)) ? xs : null;
}

export function parseSeries(data: Record<string, unknown>, max: number): SeriesData | null {
  const x = labels(data.x, max);
  if (!x) return null;
  const raw = Array.isArray(data.series) ? data.series : data.y !== undefined ? [{ name: "", y: data.y }] : [];
  const series: Series[] = [];
  for (const s of raw) {
    const rec = asRecord(s);
    const y = rec ? numbers(rec.y, max, x.length) : null;
    if (rec && y) series.push({ name: text(rec.name), y });
  }
  if (series.length === 0) return null;
  return { x, xs: numericX(data.x), series: series.slice(0, MAX_SERIES), ...axisLabels(data) };
}

export function parseBeforeAfter(data: Record<string, unknown>): SeriesData | null {
  const x = labels(data.labels, 20);
  const before = x ? numbers(data.before, 20, x.length) : null;
  const after = x ? numbers(data.after, 20, x.length) : null;
  if (!x || !before || !after) return null;
  return {
    x,
    xs: null,
    series: [
      { name: text(data.before_name) || "Before", y: before },
      { name: text(data.after_name) || "After", y: after },
    ],
    ...axisLabels(data),
  };
}

export function parseHeatmap(data: Record<string, unknown>): HeatmapData | null {
  const rows = labels(data.rows, MAX_CELLS);
  const cols = labels(data.cols, MAX_CELLS);
  const values = data.values;
  if (!rows || !cols || !Array.isArray(values) || values.length !== rows.length) return null;
  const grid: number[][] = [];
  for (const r of values) {
    const row = numbers(r, MAX_CELLS, cols.length);
    if (!row) return null;
    grid.push(row);
  }
  return { rows, cols, values: grid, ...axisLabels(data) };
}

/** Histogram bins for raw values; each bin is labelled by its left edge. */
export function histogram(values: number[], bins: number): SeriesData {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const n = Math.min(40, Math.max(2, Math.round(bins)));
  const width = (hi - lo) / n || 1;
  const counts = new Array<number>(n).fill(0);
  for (const v of values) {
    const i = Math.min(n - 1, Math.floor((v - lo) / width));
    counts[i] = (counts[i] ?? 0) + 1;
  }
  const edges = counts.map((_c, i) => Number((lo + i * width).toPrecision(3)));
  return {
    x: edges.map(String),
    xs: edges,
    series: [{ name: "Count", y: counts }],
    xLabel: "",
    yLabel: "",
  };
}

export function parseDistribution(data: Record<string, unknown>): { data: SeriesData; style: "bars" | "curve" } | null {
  if (Array.isArray(data.values)) {
    const values = numbers(data.values, MAX_SAMPLES);
    if (!values || values.length < 2) return null;
    const bins = isNum(data.bins) ? data.bins : Math.ceil(Math.sqrt(values.length));
    return { data: { ...histogram(values, bins), ...axisLabels(data) }, style: "bars" };
  }
  const d = parseSeries(data, MAX_POINTS);
  if (!d || d.x.length < 2) return null;
  return { data: d, style: data.style === "curve" ? "curve" : "bars" };
}

export function parseGraph(spec: GraphSpec): ParsedGraph | null {
  const data = asRecord(spec.data);
  if (!data) return null;
  switch (spec.template) {
    case "bar": {
      const d = parseSeries(data, MAX_BARS);
      return d ? { kind: "bar", data: d } : null;
    }
    case "line": {
      const d = parseSeries(data, MAX_POINTS);
      return d ? { kind: "line", data: d } : null;
    }
    case "before_after": {
      const d = parseBeforeAfter(data);
      return d ? { kind: "before_after", data: d, colors: ["ink2", "accent"] } : null;
    }
    case "distribution": {
      const d = parseDistribution(data);
      return d ? { kind: "distribution", data: d.data, style: d.style } : null;
    }
    case "heatmap": {
      const d = parseHeatmap(data);
      return d ? { kind: "heatmap", data: d } : null;
    }
    default:
      return null;
  }
}
