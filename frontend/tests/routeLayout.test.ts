import {
  bezierAt,
  columnOf,
  COL_GAP,
  layoutRoute,
  MARKER_GAP,
  MARKER_W,
  ROW_GAP,
  STOP_H,
  STOP_W,
  type LayoutInput,
} from "../src/route/layout";

const stops = (n: number, segments: Record<number, string> = {}): LayoutInput[] =>
  Array.from({ length: n }, (_, i) => ({
    id: `s${i + 1}`,
    section_label: `§${i + 1}`,
    segment_title: segments[i] ?? null,
  }));

test("stops alternate center → right → left → center …", () => {
  expect(Array.from({ length: 7 }, (_, i) => columnOf(i))).toEqual([0, 1, -1, 0, 1, -1, 0]);
});

test("positions: three columns, even vertical spacing", () => {
  const { stops: placed } = layoutRoute(stops(7));
  expect(placed.map((p) => p.x)).toEqual([0, COL_GAP, -COL_GAP, 0, COL_GAP, -COL_GAP, 0]);
  expect(placed.map((p) => p.y)).toEqual([0, 1, 2, 3, 4, 5, 6].map((i) => i * ROW_GAP));
  expect(placed.map((p) => p.index)).toEqual([0, 1, 2, 3, 4, 5, 6]);
});

test("a stop to the right: leaves the right side, enters the top of the next (explicit vector)", () => {
  const c = layoutRoute(stops(3)).connectors[0];
  expect(c).toBeDefined();
  if (!c) return;
  expect(c.side).toBe("right");
  expect(c.start).toEqual({ x: STOP_W / 2, y: 0 }); // 116, 0: middle of the right edge
  expect(c.tip).toEqual({ x: 320, y: 168 - STOP_H / 2 }); // top-middle of the next stop
  expect(c.end).toEqual({ x: 320, y: 112 }); // arrowhead base, 8 above the tip
  expect(c.path).toBe("M116,0 C236,0 320,52 320,112");
  expect(c.mid).toEqual({ x: 263, y: 33.5 });
});

test("a stop to the left: leaves the bottom, sweeps into the top of the next (explicit vector)", () => {
  const c = layoutRoute(stops(3)).connectors[1];
  expect(c).toBeDefined();
  if (!c) return;
  expect(c.side).toBe("bottom");
  expect(c.start).toEqual({ x: 320, y: 168 + STOP_H / 2 }); // 320, 216: middle of the bottom edge
  expect(c.tip).toEqual({ x: -320, y: 2 * 168 - STOP_H / 2 });
  expect(c.path).toBe("M320,216 C320,248 -320,248 -320,280");
  expect(c.mid).toEqual({ x: 0, y: 248 });
});

test("left → center goes to the right again", () => {
  const c = layoutRoute(stops(4)).connectors[2];
  expect(c?.side).toBe("right");
  expect(c?.start).toEqual({ x: -320 + STOP_W / 2, y: 2 * ROW_GAP });
  expect(c?.path).toBe("M-204,336 C-84,336 0,388 0,448");
});

test("every connector ends in an arrowhead pointing down into the next stop", () => {
  for (const c of layoutRoute(stops(9)).connectors) {
    const [tip, left, right] = c.arrow.split(" ").map((p) => p.split(",").map(Number));
    expect(tip).toEqual([c.tip.x, c.tip.y]);
    expect(left?.[1]).toBe(c.end.y);
    expect(right?.[1]).toBe(c.end.y);
    expect((left?.[0] ?? 0) + (right?.[0] ?? 0)).toBe(2 * c.end.x);
  }
});

test("the connector midpoint is the middle of its curve", () => {
  for (const c of layoutRoute(stops(6)).connectors) {
    const m = bezierAt(c.start, c.c1, c.c2, c.end, 0.5);
    expect(c.mid.x).toBeCloseTo(m.x, 1);
    expect(c.mid.y).toBeCloseTo(m.y, 1);
  }
});

test("a segment marker sits beside the first stop of a section, in free space", () => {
  const { markers } = layoutRoute(stops(7, { 0: "§1 Introduction", 1: "§2 Method", 2: "§3 Results" }));
  expect(markers.map((m) => [m.stopId, m.side])).toEqual([
    ["s1", "left"], // center column → left
    ["s2", "right"], // right column → right
    ["s3", "left"], // left column → outer left
  ]);
  const second = markers[1];
  expect(second?.x).toBe(COL_GAP + STOP_W / 2 + MARKER_GAP);
  const first = markers[0];
  expect(first?.x).toBe(-STOP_W / 2 - MARKER_GAP - MARKER_W);
  expect(first?.text).toBe("§1 Introduction");
});

test("bounds contain every stop and marker", () => {
  const l = layoutRoute(stops(5, { 0: "§1 Intro", 1: "§2 Method" }));
  const { x, y, width, height } = l.bounds;
  for (const p of l.stops) {
    expect(p.x - STOP_W / 2).toBeGreaterThanOrEqual(x);
    expect(p.x + STOP_W / 2).toBeLessThanOrEqual(x + width);
    expect(p.y - STOP_H / 2).toBeGreaterThanOrEqual(y);
    expect(p.y + STOP_H / 2).toBeLessThanOrEqual(y + height);
  }
  for (const m of l.markers) {
    expect(m.x).toBeGreaterThanOrEqual(x);
    expect(m.x + MARKER_W).toBeLessThanOrEqual(x + width);
  }
});

test("deterministic, and safe for empty and single-stop routes", () => {
  const input = stops(11, { 0: "§1 A", 4: "§2 B" });
  expect(JSON.stringify(layoutRoute(input))).toBe(JSON.stringify(layoutRoute(input)));
  expect(layoutRoute([]).stops).toEqual([]);
  expect(layoutRoute([]).bounds.width).toBeGreaterThan(0);
  const one = layoutRoute(stops(1));
  expect(one.connectors).toEqual([]);
  expect(one.stops).toHaveLength(1);
});
