// Query helpers the Chart needs beyond src/api/hooks: the same layer query used for hover prefetch,
// and concept titles from layers already in the cache (for breadcrumbs).

import type { QueryClient } from "@tanstack/react-query";

import { api, keys } from "../api/client";
import type { LayerPayload } from "../api/types";
import { MASTER } from "../state/store";

/** Must stay in step with useLayer (src/api/hooks.ts): same key, same data shape (null = generating). */
export function layerQuery(paperId: string, layerId: string) {
  return {
    queryKey: keys.layer(paperId, layerId),
    queryFn: async (): Promise<LayerPayload | null> => {
      const m = layerId === MASTER ? await api.master(paperId) : await api.layer(paperId, layerId);
      return m.pending ? null : m.data;
    },
    staleTime: Infinity,
  };
}

export function cachedLayer(qc: QueryClient, paperId: string, layerId: string): LayerPayload | null {
  return qc.getQueryData<LayerPayload | null>(keys.layer(paperId, layerId)) ?? null;
}

/** Title for a concept on the current path: its own layer's center, else the parent layer's entry. */
export function conceptTitle(qc: QueryClient, paperId: string, path: string[], depth: number): string {
  const id = depth === 0 ? null : path[depth - 1];
  if (id === null || id === undefined) {
    const master = cachedLayer(qc, paperId, MASTER);
    return master ? (master.concepts[master.layer.center]?.title ?? "Map") : "Map";
  }
  const own = cachedLayer(qc, paperId, id)?.concepts[id]?.title;
  if (own) return own;
  const parentId = depth >= 2 ? (path[depth - 2] ?? MASTER) : MASTER;
  return cachedLayer(qc, paperId, parentId)?.concepts[id]?.title ?? id;
}
