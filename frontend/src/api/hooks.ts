// Query hooks + the SSE listener. A 202 ("generating") resolves to `undefined` data and the query
// waits for an SSE event to invalidate it — the UI never polls in a tight loop (spec §12).

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, eventsUrl, keys, type Maybe } from "./client";
import type { Angle, ProgressEvent } from "./types";

const PENDING_RETRY_MS = 8000; // fallback if an SSE event is missed (e.g. after a reconnect)

function unwrap<T>(m: Maybe<T>): T | null {
  return m.pending ? null : m.data;
}

function pendingRefetch(data: unknown): number | false {
  return data === null ? PENDING_RETRY_MS : false;
}

export function usePaper(paperId: string | null) {
  return useQuery({
    queryKey: keys.paper(paperId ?? ""),
    queryFn: async () => unwrap(await api.paper(paperId ?? "")),
    enabled: paperId !== null,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useLayer(paperId: string | null, layerId: string) {
  return useQuery({
    queryKey: keys.layer(paperId ?? "", layerId),
    queryFn: async () =>
      unwrap(await (layerId === "master" ? api.master(paperId ?? "") : api.layer(paperId ?? "", layerId))),
    enabled: paperId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useConcept(paperId: string | null, conceptId: string | null) {
  return useQuery({
    queryKey: keys.concept(paperId ?? "", conceptId ?? ""),
    queryFn: async () => unwrap(await api.concept(paperId ?? "", conceptId ?? "")),
    enabled: paperId !== null && conceptId !== null,
  });
}

export function useExplanation(paperId: string | null, conceptId: string | null, angle: Angle) {
  return useQuery({
    queryKey: keys.explanation(paperId ?? "", conceptId ?? "", angle),
    queryFn: async () => unwrap(await api.explanation(paperId ?? "", conceptId ?? "", angle)),
    enabled: paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useEdge(paperId: string | null, linkId: string | null) {
  return useQuery({
    queryKey: keys.edge(paperId ?? "", linkId ?? ""),
    queryFn: async () => unwrap(await api.edge(paperId ?? "", linkId ?? "")),
    enabled: paperId !== null && linkId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useCode(paperId: string | null, conceptId: string | null) {
  return useQuery({
    queryKey: keys.code(paperId ?? "", conceptId ?? ""),
    queryFn: async () => unwrap(await api.code(paperId ?? "", conceptId ?? "")),
    enabled: paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useExample(paperId: string | null, conceptId: string | null, variant: number) {
  return useQuery({
    queryKey: keys.example(paperId ?? "", conceptId ?? "", variant),
    queryFn: async () => unwrap(await api.example(paperId ?? "", conceptId ?? "", variant)),
    enabled: paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useGraph(paperId: string | null, conceptId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.graph(paperId ?? "", conceptId ?? ""),
    queryFn: async () => unwrap(await api.graph(paperId ?? "", conceptId ?? "")),
    enabled: enabled && paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data),
  });
}

export function useBudget() {
  return useQuery({
    queryKey: keys.budget(),
    queryFn: async () => unwrap(await api.budget()),
    refetchInterval: 30_000,
  });
}

export interface LiveState {
  progress: ProgressEvent | null;
  rateLimitedUntil: number | null; // epoch ms
  lastError: string | null;
}

/** Listen to a paper's SSE stream; invalidate queries as results land. */
export function usePaperEvents(paperId: string | null): LiveState {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>({ progress: null, rateLimitedUntil: null, lastError: null });

  useEffect(() => {
    if (paperId === null || typeof EventSource === "undefined") return;
    const source = new EventSource(eventsUrl(paperId));
    const parse = (e: MessageEvent<string>): Record<string, unknown> => {
      try {
        return JSON.parse(e.data) as Record<string, unknown>;
      } catch {
        return {};
      }
    };
    source.addEventListener("progress", (e) => {
      const data = parse(e as MessageEvent<string>) as unknown as ProgressEvent;
      setState((s) => ({ ...s, progress: data }));
      if (data.stage === "reading" || data.stage === "finding_code") {
        void qc.invalidateQueries({ queryKey: keys.paper(paperId) });
      }
    });
    source.addEventListener("layer_ready", (e) => {
      const data = parse(e as MessageEvent<string>);
      void qc.invalidateQueries({ queryKey: keys.layer(paperId, String(data.layer_id)) });
      void qc.invalidateQueries({ queryKey: keys.paper(paperId) });
      void qc.invalidateQueries({ queryKey: ["concept", paperId] });
    });
    source.addEventListener("detail_ready", (e) => {
      const data = parse(e as MessageEvent<string>);
      if (data.part === "edge") {
        void qc.invalidateQueries({ queryKey: keys.edge(paperId, String(data.link_id)) });
      } else if (data.part === "code" || data.part === "example" || data.part === "graph") {
        void qc.invalidateQueries({ queryKey: [data.part, paperId, String(data.concept_id)] });
        void qc.invalidateQueries({ queryKey: keys.concept(paperId, String(data.concept_id)) });
      } else {
        void qc.invalidateQueries({ queryKey: ["explanation", paperId, String(data.concept_id)] });
        void qc.invalidateQueries({ queryKey: keys.concept(paperId, String(data.concept_id)) });
      }
      void qc.invalidateQueries({ queryKey: keys.budget() });
    });
    source.addEventListener("rate_limited", (e) => {
      const data = parse(e as MessageEvent<string>);
      const wait = Number(data.wait_seconds ?? 10);
      setState((s) => ({ ...s, rateLimitedUntil: Date.now() + wait * 1000 }));
    });
    source.addEventListener("error", (e) => {
      const msg = (e as MessageEvent<string>).data;
      if (typeof msg === "string" && msg) {
        const data = parse(e as MessageEvent<string>);
        setState((s) => ({ ...s, lastError: String(data.message ?? "Something went wrong.") }));
      }
    });
    return () => source.close();
  }, [paperId, qc]);

  return state;
}
