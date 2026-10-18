// Query helpers the Route needs beyond src/api/hooks: the same route query used for prefetching,
// cache lookups for breadcrumbs, and the concepts behind the stops (grounding glyph, mismatch mark).

import { useQueries, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, keys } from "../api/client";
import type { Concept, ConceptPayload, LayerPayload, Route } from "../api/types";

export const MAIN = "main";

/** Must stay in step with useRoute (src/api/hooks.ts): same key, same data shape (null = generating). */
export function routeQuery(paperId: string, routeId: string) {
  return {
    queryKey: keys.route(paperId, routeId),
    queryFn: async (): Promise<Route | null> => {
      const m = await (routeId === MAIN ? api.routeMain(paperId) : api.routeSub(paperId, routeId));
      return m.pending ? null : m.data;
    },
    staleTime: Infinity,
  };
}

export function cachedRoute(qc: QueryClient, paperId: string, routeId: string): Route | null {
  return qc.getQueryData<Route | null>(keys.route(paperId, routeId)) ?? null;
}

/** Title of the stop `stopId` in route `routeId`, from the cache (falls back to the id). */
export function stopTitle(qc: QueryClient, paperId: string, routeId: string, stopId: string): string {
  return cachedRoute(qc, paperId, routeId)?.stops.find((s) => s.id === stopId)?.title ?? stopId;
}

function fromLayers(qc: QueryClient, paperId: string, id: string): Concept | undefined {
  for (const [, data] of qc.getQueriesData<LayerPayload | null>({ queryKey: ["layer", paperId] })) {
    const c = data?.concepts[id];
    if (c) return c;
  }
  return undefined;
}

/** The concepts behind a route's stops, by id: from the Chart's cached layers when present,
 * otherwise from the (cheap, database-only) concept endpoint. */
export function useStopConcepts(paperId: string | null, route: Route | null): Record<string, Concept> {
  const qc = useQueryClient();
  const ids = Array.from(new Set((route?.stops ?? []).flatMap((s) => (s.concept_id ? [s.concept_id] : []))));
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: keys.concept(paperId ?? "", id),
      queryFn: async (): Promise<ConceptPayload | null> => {
        const m = await api.concept(paperId ?? "", id);
        return m.pending ? null : m.data;
      },
      enabled: paperId !== null && fromLayers(qc, paperId, id) === undefined,
      staleTime: 60_000,
    })),
  });
  const out: Record<string, Concept> = {};
  ids.forEach((id, i) => {
    const c = (paperId ? fromLayers(qc, paperId, id) : undefined) ?? results[i]?.data?.concept;
    if (c) out[id] = c;
  });
  // Keep what we have learned (a sub-route's concepts shouldn't vanish while the main route fades
  // out) and return the same object until something changes, so nodes aren't rebuilt every render.
  const known = useRef<Record<string, Concept>>({});
  const merged = { ...known.current, ...out };
  const same =
    Object.keys(merged).length === Object.keys(known.current).length &&
    Object.keys(merged).every((k) => known.current[k] === merged[k]);
  if (!same) known.current = merged;
  return known.current;
}
