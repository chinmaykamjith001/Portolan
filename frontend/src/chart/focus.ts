// "Show on Chart": which layers do you jump through to reach a concept? Walks `also_appears_in`
// (the layers a concept belongs to) upward until the master layer, always taking the shallowest parent.

import type { QueryClient } from "@tanstack/react-query";

import { api, keys } from "../api/client";
import type { ConceptPayload } from "../api/types";
import { MASTER } from "../state/store";

const MAX_DEPTH = 16;

/** The `path` (outermost first) that puts `conceptId` on screen, [] for the master layer, or null if
 * the concept can't be placed. Uses the same concept query as `useConcept`, so the cache is shared. */
export async function resolveChartPath(
  qc: QueryClient,
  paperId: string,
  conceptId: string,
): Promise<string[] | null> {
  const load = (id: string) =>
    qc.fetchQuery({
      queryKey: keys.concept(paperId, id),
      queryFn: async (): Promise<ConceptPayload | null> => {
        const m = await api.concept(paperId, id);
        return m.pending ? null : m.data;
      },
      staleTime: Infinity,
    });

  const chain: string[] = [];
  const seen = new Set<string>([conceptId]);
  let current = conceptId;
  for (let i = 0; i < MAX_DEPTH; i++) {
    const payload = await load(current).catch(() => null);
    if (!payload) return null;
    const layers = payload.also_appears_in.filter((l) => l !== current);
    if (layers.includes(MASTER)) return chain;
    if (layers.length === 0) return payload.concept.depth === 0 ? chain : null;
    let best: { id: string; depth: number } | null = null;
    for (const id of layers) {
      if (seen.has(id)) continue;
      const c = await load(id).catch(() => null);
      if (!c) continue;
      const depth = c.concept.depth;
      if (best === null || depth < best.depth || (depth === best.depth && id < best.id)) best = { id, depth };
    }
    if (best === null) return null;
    chain.unshift(best.id);
    seen.add(best.id);
    current = best.id;
  }
  return null;
}
