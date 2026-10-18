// The Route's S-shaped layout (spec §13.5). Pure and deterministic: stops alternate across three
// columns (center → right → left → center …) with even vertical spacing, joined by curved
// connectors. Everything is in flow coordinates; a stop's (x, y) is its CENTER.
//
// Connector rule (by where the next stop is):
//   next is to the right → leaves the RIGHT side of this stop, enters the TOP of the next;
//   next is to the left  → leaves the BOTTOM of this stop and sweeps into the TOP of the next.

export const STOP_W = 232;
export const STOP_H = 96;
export const COL_GAP = 320; // center-to-center distance between neighbouring columns
export const ROW_GAP = 168; // center-to-center distance between consecutive stops
export const MARKER_W = 190;
export const MARKER_H = 40;
export const MARKER_GAP = 20;
export const ARROW_LEN = 8;
export const ARROW_HALF = 4;

export type Column = -1 | 0 | 1;

export interface Pt {
  x: number;
  y: number;
}

export interface LayoutInput {
  id: string;
  section_label: string;
  segment_title: string | null;
}

export interface PlacedStop {
  id: string;
  index: number; // 0-based position along the route
  col: Column;
  x: number;
  y: number;
}

export interface Connector {
  id: string; // "<from>-><to>"
  fromId: string;
  toId: string;
  fromIndex: number;
  toIndex: number;
  /** Which side of the current stop it leaves from. */
  side: "right" | "bottom";
  start: Pt;
  c1: Pt;
  c2: Pt;
  /** Where the line ends: the arrowhead's base. The tip is `tip`. */
  end: Pt;
  tip: Pt;
  path: string;
  arrow: string; // polygon points
  /** Middle of the curve: where the transition text sits. */
  mid: Pt;
}

export interface Marker {
  stopId: string;
  text: string;
  side: "left" | "right";
  /** Top-left corner of the marker box. */
  x: number;
  y: number;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface RouteLayout {
  stops: PlacedStop[];
  connectors: Connector[];
  markers: Marker[];
  bounds: Bounds;
}

const COLUMNS: Column[] = [0, 1, -1];

export function columnOf(index: number): Column {
  return COLUMNS[index % 3] ?? 0;
}

const r1 = (n: number) => Math.round(n * 10) / 10;

/** Point on a cubic Bézier at t. */
export function bezierAt(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt {
  const u = 1 - t;
  const a = u * u * u;
  const b = 3 * u * u * t;
  const c = 3 * u * t * t;
  const d = t * t * t;
  return { x: a * p0.x + b * p1.x + c * p2.x + d * p3.x, y: a * p0.y + b * p1.y + c * p2.y + d * p3.y };
}

export function connect(from: PlacedStop, to: PlacedStop): Connector {
  const tip: Pt = { x: to.x, y: to.y - STOP_H / 2 };
  const end: Pt = { x: tip.x, y: tip.y - ARROW_LEN };
  const toTheRight = to.x > from.x;
  const side = toTheRight ? "right" : "bottom";
  const start: Pt = toTheRight
    ? { x: from.x + STOP_W / 2, y: from.y }
    : { x: from.x, y: from.y + STOP_H / 2 };
  let c1: Pt;
  let c2: Pt;
  if (toTheRight) {
    // Out to the right, then curve down into the top of the next stop.
    const k = Math.min(120, Math.abs(end.x - start.x) * 0.7 + 20);
    const m = Math.min(60, (end.y - start.y) * 0.6);
    c1 = { x: start.x + k, y: start.y };
    c2 = { x: end.x, y: end.y - m };
  } else {
    // Down, sweep across, down into the top of the next stop.
    const k = (end.y - start.y) / 2;
    c1 = { x: start.x, y: start.y + k };
    c2 = { x: end.x, y: end.y - k };
  }
  const mid = bezierAt(start, c1, c2, end, 0.5);
  const arrow = [
    tip,
    { x: end.x - ARROW_HALF, y: end.y },
    { x: end.x + ARROW_HALF, y: end.y },
  ]
    .map((p) => `${r1(p.x)},${r1(p.y)}`)
    .join(" ");
  return {
    id: `${from.id}->${to.id}`,
    fromId: from.id,
    toId: to.id,
    fromIndex: from.index,
    toIndex: to.index,
    side,
    start,
    c1,
    c2,
    end,
    tip,
    path: `M${r1(start.x)},${r1(start.y)} C${r1(c1.x)},${r1(c1.y)} ${r1(c2.x)},${r1(c2.y)} ${r1(end.x)},${r1(end.y)}`,
    arrow,
    mid: { x: r1(mid.x), y: r1(mid.y) },
  };
}

export function layoutRoute(inputs: readonly LayoutInput[]): RouteLayout {
  const stops: PlacedStop[] = inputs.map((s, index) => {
    const col = columnOf(index);
    return { id: s.id, index, col, x: col * COL_GAP, y: index * ROW_GAP };
  });

  const connectors: Connector[] = [];
  for (let i = 0; i + 1 < stops.length; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (a && b) connectors.push(connect(a, b));
  }

  // Segment markers sit in the free space beside the first stop of each section: to the right of a
  // right-column stop, to the left of the others (nothing else is in that row).
  const markers: Marker[] = [];
  inputs.forEach((s, i) => {
    const p = stops[i];
    if (!s.segment_title || !p) return;
    const side = p.col > 0 ? "right" : "left";
    markers.push({
      stopId: s.id,
      text: s.segment_title,
      side,
      x: side === "right" ? p.x + STOP_W / 2 + MARKER_GAP : p.x - STOP_W / 2 - MARKER_GAP - MARKER_W,
      y: p.y - MARKER_H / 2,
    });
  });

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const grow = (x0: number, y0: number, x1: number, y1: number) => {
    minX = Math.min(minX, x0);
    minY = Math.min(minY, y0);
    maxX = Math.max(maxX, x1);
    maxY = Math.max(maxY, y1);
  };
  for (const p of stops) grow(p.x - STOP_W / 2, p.y - STOP_H / 2, p.x + STOP_W / 2, p.y + STOP_H / 2);
  for (const m of markers) grow(m.x, m.y, m.x + MARKER_W, m.y + MARKER_H);
  const bounds: Bounds =
    stops.length === 0
      ? { x: 0, y: 0, width: 1, height: 1 }
      : { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
  return { stops, connectors, markers, bounds };
}
