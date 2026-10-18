// A deterministic synthetic layer for performance checks and tests (spec §18 M5: 60 fps pan with a
// 300-node layer). Not used by the app itself.

import type { Concept, Grounding, LayerPayload, Link } from "../api/types";

const GROUNDINGS: Grounding[] = ["concept_only", "in_code", "in_library", "shown_by_running", "unknown"];

export function syntheticLayer(n: number, layerId = "master"): LayerPayload {
  const ids = Array.from({ length: n }, (_, i) => (i === 0 ? "c0" : `c${i}`));
  const concepts: Record<string, Concept> = {};
  ids.forEach((id, i) => {
    concepts[id] = {
      id,
      canonical_name: id,
      title: i === 0 ? "Synthetic center" : `Concept ${i}`,
      gist: `A synthetic concept number ${i}, used to measure panning and zooming.`,
      grounding: GROUNDINGS[i % GROUNDINGS.length] ?? "concept_only",
      is_first_principle: i % 7 === 3,
      depth: i === 0 ? 0 : 1,
      anchors: [],
      has_layer: i % 7 !== 3 && i % 2 === 0,
      gen_state: "skeleton",
      issue_count_self: i % 37 === 5 ? 1 : 0,
    };
  });
  // A tree with branching factor 4 plus a few cross links: connected, like a real layer.
  const links: Link[] = [];
  for (let i = 1; i < n; i++) {
    const parent = ids[Math.floor((i - 1) / 4)] ?? "c0";
    links.push({ id: `${layerId}:${parent}->c${i}`, layer_id: layerId, source: parent, target: `c${i}`, label: "uses", kind: "uses" });
  }
  for (let i = 5; i < n; i += 9) {
    links.push({ id: `${layerId}:c${i}->c${i - 3}`, layer_id: layerId, source: `c${i}`, target: `c${i - 3}`, label: "enables", kind: "enables" });
  }
  return {
    layer: { id: layerId, paper_id: "synthetic", center: "c0", concepts: ids.slice(1), links, gen_state: "skeleton" },
    concepts,
  };
}
