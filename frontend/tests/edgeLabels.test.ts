// Edge labels must fit between the two shapes they connect (a node must never cover one).
import ELK from "elkjs/lib/elk.bundled.js";

import type { Concept, LayerPayload } from "../src/api/types";
import {
  CENTER_SIZE,
  NODE_SIZE,
  computeLayout,
  edgeLengthFor,
  layoutInputFor,
  type ElkLike,
} from "../src/chart/layout";

const elk = () => Promise.resolve(new ELK() as unknown as ElkLike); // same as chart.test.tsx

function concept(id: string, title: string): Concept {
  return {
    id, canonical_name: title.toLowerCase(), title, gist: `${title}.`, grounding: "unknown",
    is_first_principle: false, depth: 1, anchors: [], has_layer: false, gen_state: "skeleton",
    issue_count_self: 0,
  };
}

// The labels Gemini actually wrote for a 13-concept map in a real run.
const LABELS = [
  "stacks layers containing", "computes parallel heads", "scales logits using",
  "ignores invalid tokens with", "normalizes outputs with", "regularizes loss through",
  "receives regularized activations from", "wraps sub-layers in", "removes recurrent dependencies to achieve",
  "is evaluated on", "is optimized by", "schedules step sizes with", "alternates attention with",
];

function realisticLayer(): LayerPayload {
  const ids = LABELS.map((_, i) => `c${i}`);
  const concepts: Record<string, Concept> = { center: concept("center", "Toyformer") };
  ids.forEach((id, i) => (concepts[id] = concept(id, `Idea ${i}`)));
  // A star with a few chains, like the real map.
  const links = LABELS.map((label, i) => {
    const source = i % 4 === 3 ? `c${i - 1}` : "center";
    return { id: `m:${source}->c${i}`, layer_id: "master", source, target: `c${i}`, label, kind: "uses" as const };
  });
  return {
    layer: { id: "master", paper_id: "p", center: "center", concepts: ids, links, gen_state: "detailed" },
    concepts,
  };
}

test("a long label asks for a longer edge, a short one keeps the default", () => {
  expect(edgeLengthFor(NODE_SIZE, NODE_SIZE, "")).toBe(225);
  expect(edgeLengthFor(NODE_SIZE, NODE_SIZE, "uses")).toBe(225);
  const long = edgeLengthFor(NODE_SIZE, NODE_SIZE, "removes recurrent dependencies to achieve");
  expect(long).toBe(NODE_SIZE + 160 + 28);
  expect(edgeLengthFor(CENTER_SIZE, NODE_SIZE, "removes recurrent dependencies to achieve")).toBeGreaterThan(long);
});

test("every label fits in the gap between the shapes it connects", async () => {
  const payload = realisticLayer();
  const pos = await computeLayout(layoutInputFor(payload), elk);
  const size = (id: string) => (id === "center" ? CENTER_SIZE : NODE_SIZE);
  const short: string[] = [];
  for (const l of payload.layer.links) {
    const a = pos[l.source];
    const b = pos[l.target];
    if (!a || !b) throw new Error("missing position");
    const gap = Math.hypot(a.x - b.x, a.y - b.y) - size(l.source) / 2 - size(l.target) / 2;
    const width = Math.min(160, Math.ceil(l.label.length * 6.2));
    if (gap < width * 0.9) short.push(`${l.label}: gap ${Math.round(gap)} < label ${width}`);
  }
  expect(short).toEqual([]);
});
