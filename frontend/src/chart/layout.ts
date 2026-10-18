// Pure, deterministic layout for one Chart layer (spec §13.2). Shared by the Web Worker and the
// main-thread fallback (tests, browsers without workers). Positions are node CENTERS with the
// layer's center concept pinned at (0, 0).

import type { Grounding, LayerPayload } from "../api/types";

export const NODE_SIZE = 156;
export const CENTER_SIZE = 196;
/** ELK stress is O(n²) per iteration; above this we use the ring layout (still deterministic). */
export const ELK_MAX_NODES = 60;
const EDGE_LENGTH = 225;
const GAP = 24;
// Edge labels (chart.module.css .edgeLabel): 12px italic, at most 160px wide, wrapping.
const LABEL_MAX_WIDTH = 160;
const LABEL_CHAR_WIDTH = 6.2;
const LABEL_MARGIN = 28; // padding on both sides plus the arrowhead

/** Center-to-center length that leaves room for the link's label between the two shapes, so a
 * node never covers it. Never shorter than the default. */
export function edgeLengthFor(sizeA: number, sizeB: number, label: string | undefined): number {
  const text = label?.trim() ?? "";
  if (!text) return EDGE_LENGTH;
  const width = Math.min(LABEL_MAX_WIDTH, Math.ceil(text.length * LABEL_CHAR_WIDTH));
  return Math.max(EDGE_LENGTH, Math.round(sizeA / 2 + sizeB / 2 + width + LABEL_MARGIN));
}

export interface LayoutNode {
  id: string;
  size: number;
}

export interface LayoutInput {
  center: string;
  nodes: LayoutNode[];
  edges: { source: string; target: string; label?: string }[];
}

export type Positions = Record<string, { x: number; y: number }>;

/** The subset of the elkjs API we use (so tests can pass any ELK instance). */
export interface ElkLike {
  layout(graph: ElkGraph): Promise<ElkGraph>;
}

export interface ElkEdge {
  id: string;
  sources: string[];
  targets: string[];
  layoutOptions?: Record<string, string>;
}

export interface ElkGraph {
  id: string;
  layoutOptions?: Record<string, string>;
  children?: { id: string; width: number; height: number; x?: number; y?: number; layoutOptions?: Record<string, string> }[];
  edges?: ElkEdge[];
}

export function sizeOf(isCenter: boolean): number {
  return isCenter ? CENTER_SIZE : NODE_SIZE;
}

/** Build the layout input from a layer payload: center first, then the layer's concepts in order. */
export function layoutInputFor(payload: LayerPayload): LayoutInput {
  const { layer, concepts } = payload;
  const ids = [layer.center, ...layer.concepts.filter((id) => id !== layer.center)].filter(
    (id, i, all) => concepts[id] !== undefined && all.indexOf(id) === i,
  );
  const present = new Set(ids);
  return {
    center: layer.center,
    nodes: ids.map((id) => ({ id, size: sizeOf(id === layer.center) })),
    edges: layer.links
      .filter((l) => present.has(l.source) && present.has(l.target) && l.source !== l.target)
      .map((l) => ({ source: l.source, target: l.target, label: l.label })),
  };
}

/** A stable key for the position cache: same concepts and links → same picture. */
export function layoutKey(paperId: string, layerId: string, input: LayoutInput): string {
  const nodes = input.nodes.map((n) => n.id).join(",");
  const edges = input.edges.map((e) => `${e.source}>${e.target}:${e.label ?? ""}`).join(",");
  return `${paperId}|${layerId}|${nodes}|${edges}`;
}

/** Breadth-first distance from the center (unreachable nodes are absent). */
function bfs(input: LayoutInput): Map<string, number> {
  const adj = new Map<string, string[]>(input.nodes.map((n) => [n.id, []]));
  for (const e of input.edges) {
    adj.get(e.source)?.push(e.target);
    adj.get(e.target)?.push(e.source);
  }
  const dist = new Map<string, number>([[input.center, 0]]);
  const queue = [input.center];
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i] as string;
    for (const next of adj.get(id) ?? []) {
      if (!dist.has(next)) {
        dist.set(next, (dist.get(id) ?? 0) + 1);
        queue.push(next);
      }
    }
  }
  return dist;
}

/** Distance from the center; nodes unreachable from it count as distance 1. */
export function ringsFromCenter(input: LayoutInput): Map<string, number> {
  const dist = bfs(input);
  for (const n of input.nodes) if (!dist.has(n.id)) dist.set(n.id, 1);
  return dist;
}

/** Concentric rings by distance from the center, in input order. Used for big layers and as ELK's start. */
export function ringLayout(input: LayoutInput): Positions {
  const dist = ringsFromCenter(input);
  const rings = new Map<number, LayoutNode[]>();
  for (const n of input.nodes) {
    if (n.id === input.center) continue;
    const d = dist.get(n.id) ?? 1;
    rings.set(d, [...(rings.get(d) ?? []), n]);
  }
  const out: Positions = { [input.center]: { x: 0, y: 0 } };
  let radius = 0;
  for (const d of [...rings.keys()].sort((a, b) => a - b)) {
    const ring = rings.get(d) ?? [];
    const spacing = NODE_SIZE + GAP;
    radius = Math.max(radius + EDGE_LENGTH, (ring.length * spacing) / (2 * Math.PI));
    ring.forEach((n, i) => {
      // Offset alternate rings by half a step so spokes don't line up.
      const a = -Math.PI / 2 + ((i + (d % 2 ? 0 : 0.5)) / ring.length) * 2 * Math.PI;
      out[n.id] = { x: Math.round(radius * Math.cos(a)), y: Math.round(radius * Math.sin(a)) };
    });
  }
  return out;
}

export function toElkGraph(input: LayoutInput): ElkGraph {
  const start = ringLayout(input);
  const reach = bfs(input);
  const size = new Map(input.nodes.map((n) => [n.id, n.size]));
  const edges: ElkEdge[] = input.edges.map((e, i) => ({
    id: `e${i}`,
    sources: [e.source],
    targets: [e.target],
    layoutOptions: {
      "elk.stress.desiredEdgeLength": String(
        edgeLengthFor(size.get(e.source) ?? NODE_SIZE, size.get(e.target) ?? NODE_SIZE, e.label),
      ),
    },
  }));
  // Tie unlinked concepts to the center so stress keeps them near it instead of packing them aside.
  input.nodes.forEach((n, i) => {
    if (!reach.has(n.id)) {
      edges.push({ id: `v${i}`, sources: [input.center], targets: [n.id] });
    }
  });
  return {
    id: "layer",
    layoutOptions: {
      "elk.algorithm": "stress",
      "elk.stress.desiredEdgeLength": String(EDGE_LENGTH),
      "elk.separateConnectedComponents": "false",
    },
    children: input.nodes.map((n) => {
      const p = start[n.id] ?? { x: 0, y: 0 };
      return {
        id: n.id,
        width: n.size,
        height: n.size,
        x: p.x - n.size / 2,
        y: p.y - n.size / 2,
        ...(n.id === input.center ? { layoutOptions: { "elk.stress.fixed": "true" } } : {}),
      };
    }),
    edges,
  };
}

/** ELK output → centers, translated so the center concept sits at (0, 0), overlaps removed. */
export function fromElk(input: LayoutInput, result: ElkGraph): Positions {
  const raw: Positions = {};
  for (const c of result.children ?? []) {
    raw[c.id] = { x: (c.x ?? 0) + c.width / 2, y: (c.y ?? 0) + c.height / 2 };
  }
  const origin = raw[input.center] ?? { x: 0, y: 0 };
  const out: Positions = {};
  for (const n of input.nodes) {
    const p = raw[n.id] ?? { x: 0, y: 0 };
    out[n.id] = { x: p.x - origin.x, y: p.y - origin.y };
  }
  return separate(input, out);
}

/** Push overlapping nodes apart (deterministic: fixed order, the center never moves). */
export function separate(input: LayoutInput, pos: Positions, passes = 60): Positions {
  const nodes = input.nodes;
  const out: Positions = {};
  for (const n of nodes) out[n.id] = { ...(pos[n.id] ?? { x: 0, y: 0 }) };
  for (let pass = 0; pass < passes; pass++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i] as LayoutNode;
        const b = nodes[j] as LayoutNode;
        const pa = out[a.id] as { x: number; y: number };
        const pb = out[b.id] as { x: number; y: number };
        const min = (a.size + b.size) / 2 + GAP;
        let dx = pb.x - pa.x;
        let dy = pb.y - pa.y;
        let d = Math.hypot(dx, dy);
        if (d >= min) continue;
        if (d < 1e-6) {
          const angle = (j * 2.399963) % (2 * Math.PI); // golden angle: stable, spread out
          dx = Math.cos(angle);
          dy = Math.sin(angle);
          d = 1;
        }
        const push = min - d;
        const ux = dx / d;
        const uy = dy / d;
        if (a.id === input.center) {
          pb.x += ux * push;
          pb.y += uy * push;
        } else if (b.id === input.center) {
          pa.x -= ux * push;
          pa.y -= uy * push;
        } else {
          pa.x -= (ux * push) / 2;
          pa.y -= (uy * push) / 2;
          pb.x += (ux * push) / 2;
          pb.y += (uy * push) / 2;
        }
        moved = true;
      }
    }
    if (!moved) break;
  }
  for (const id of Object.keys(out)) {
    const p = out[id] as { x: number; y: number };
    out[id] = { x: Math.round(p.x), y: Math.round(p.y) };
  }
  return out;
}

export async function computeLayout(input: LayoutInput, elk: () => Promise<ElkLike>): Promise<Positions> {
  if (input.nodes.length <= 1) return { [input.center]: { x: 0, y: 0 } };
  if (input.nodes.length > ELK_MAX_NODES) return separate(input, ringLayout(input), 4);
  const result = await (await elk()).layout(toElkGraph(input));
  return fromElk(input, result);
}

/** Boundary distance from a node's center along direction (ux, uy), per its legend shape. */
export function boundaryDistance(grounding: Grounding, size: number, ux: number, uy: number): number {
  const h = size / 2;
  if (grounding === "in_code") {
    const m = Math.max(Math.abs(ux), Math.abs(uy));
    return Math.min(h / (m || 1), h * 1.22); // rounded corners cut the diagonal a little
  }
  if (grounding === "in_library") {
    // Pointy-top hexagon with circumradius h: faces have normals at 0°, 60°, 120°, …
    const apothem = h * Math.cos(Math.PI / 6);
    const theta = Math.atan2(uy, ux);
    const sector = Math.PI / 3;
    const m = ((theta % sector) + sector) % sector;
    return apothem / Math.cos(Math.min(m, sector - m)); // 0 at a face normal, 30° at a vertex
  }
  return h;
}
