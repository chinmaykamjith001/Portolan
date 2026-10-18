// Query hooks + the SSE listener. A 202 ("generating") resolves to `undefined` data and the query
// waits for an SSE event to invalidate it — the UI never polls in a tight loop (spec §12).

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, api, eventsUrl, keys, type Maybe } from "./client";
import type { Angle, ProgressEvent } from "./types";

const PENDING_RETRY_MS = 8000; // fallback if an SSE event is missed (e.g. after a reconnect)

function unwrap<T>(m: Maybe<T>): T | null {
  return m.pending ? null : m.data;
}

/** Poll while the backend says "generating"; never after an error (only "Try again" retries). */
function pendingRefetch(data: unknown, status?: string): number | false {
  return status !== "error" && data === null ? PENDING_RETRY_MS : false;
}

export function usePaper(paperId: string | null) {
  return useQuery({
    queryKey: keys.paper(paperId ?? ""),
    queryFn: async () => unwrap(await api.paper(paperId ?? "")),
    enabled: paperId !== null,
    // Until the map exists (or a step has failed) keep checking: it is a cheap database read.
    refetchInterval: (q) => {
      const d = q.state.data;
      if (q.state.status === "error" || !d) return d === null ? PENDING_RETRY_MS : false;
      if (Object.keys(d.failures ?? {}).length > 0) return false;
      const running = Object.values(d.jobs ?? {}).some((state) => state === "queued" || state === "running");
      return d.status === "processing" || !d.chart_ready || running ? PENDING_RETRY_MS : false;
    },
  });
}

export function useLayer(paperId: string | null, layerId: string) {
  return useQuery({
    queryKey: keys.layer(paperId ?? "", layerId),
    queryFn: async () =>
      unwrap(await (layerId === "master" ? api.master(paperId ?? "") : api.layer(paperId ?? "", layerId))),
    enabled: paperId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
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
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useEdge(paperId: string | null, linkId: string | null) {
  return useQuery({
    queryKey: keys.edge(paperId ?? "", linkId ?? ""),
    queryFn: async () => unwrap(await api.edge(paperId ?? "", linkId ?? "")),
    enabled: paperId !== null && linkId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useCode(paperId: string | null, conceptId: string | null) {
  return useQuery({
    queryKey: keys.code(paperId ?? "", conceptId ?? ""),
    queryFn: async () => unwrap(await api.code(paperId ?? "", conceptId ?? "")),
    enabled: paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useExample(paperId: string | null, conceptId: string | null, variant: number) {
  return useQuery({
    queryKey: keys.example(paperId ?? "", conceptId ?? "", variant),
    queryFn: async () => unwrap(await api.example(paperId ?? "", conceptId ?? "", variant)),
    enabled: paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useGraph(paperId: string | null, conceptId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.graph(paperId ?? "", conceptId ?? ""),
    queryFn: async () => unwrap(await api.graph(paperId ?? "", conceptId ?? "")),
    enabled: enabled && paperId !== null && conceptId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useRoute(paperId: string | null, routeId: string) {
  return useQuery({
    queryKey: keys.route(paperId ?? "", routeId),
    queryFn: async () =>
      unwrap(await (routeId === "main" ? api.routeMain(paperId ?? "") : api.routeSub(paperId ?? "", routeId))),
    enabled: paperId !== null,
    staleTime: Infinity,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useFindings(paperId: string | null) {
  return useQuery({
    queryKey: keys.findings(paperId ?? ""),
    queryFn: async () => unwrap(await api.findings(paperId ?? "")),
    enabled: paperId !== null,
  });
}

export function useFaq(paperId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.faq(paperId ?? ""),
    queryFn: async () => unwrap(await api.faq(paperId ?? "")),
    enabled: enabled && paperId !== null,
    refetchInterval: (q) => pendingRefetch(q.state.data, q.state.status),
  });
}

export function useBudget() {
  return useQuery({
    queryKey: keys.budget(),
    queryFn: async () => unwrap(await api.budget()),
    refetchInterval: 30_000,
  });
}

/** The Settings dialog's data. An older backend without the endpoint resolves to `null`, and the
 * dialog falls back to what /api/budget reports. */
export function useSettings(enabled = true) {
  return useQuery({
    queryKey: keys.settings(),
    queryFn: async () => {
      try {
        return unwrap(await api.settings());
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
    enabled,
    staleTime: 60_000,
    retry: false,
  });
}

/** Is the backend up? Drives the first-run "start it with make dev" sentence. */
export function useHealth() {
  return useQuery({
    queryKey: keys.health(),
    queryFn: async () => unwrap(await api.health()),
    retry: false,
    refetchInterval: (q) => (q.state.status === "error" ? 5000 : false),
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
    setState({ progress: null, rateLimitedUntil: null, lastError: null }); // a new paper starts clean
    if (paperId === null || typeof EventSource === "undefined") return;
    let source: EventSource;
    let closed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let backoff = 1000;
    const recovered = () => setState((s) => (s.lastError === null ? s : { ...s, lastError: null }));
    const parse = (e: MessageEvent<string>): Record<string, unknown> => {
      try {
        return JSON.parse(e.data) as Record<string, unknown>;
      } catch {
        return {};
      }
    };
    const connect = () => {
      source = new EventSource(eventsUrl(paperId));
      source.addEventListener("open", () => {
        backoff = 1000;
      });
      source.addEventListener("progress", (e) => {
        const data = parse(e as MessageEvent<string>) as unknown as ProgressEvent;
        setState((s) => ({ ...s, progress: data }));
        if (data.stage === "reading" || data.stage === "finding_code") {
          void qc.invalidateQueries({ queryKey: keys.paper(paperId) });
        }
      });
      source.addEventListener("layer_ready", (e) => {
        const data = parse(e as MessageEvent<string>);
        recovered();
        void qc.invalidateQueries({ queryKey: keys.layer(paperId, String(data.layer_id)) });
        // The parent layer's payload says whether this child can be jumped into: refresh visible ones.
        void qc.invalidateQueries({ queryKey: ["layer", paperId], refetchType: "active" });
        void qc.invalidateQueries({ queryKey: keys.paper(paperId) });
        void qc.invalidateQueries({ queryKey: ["concept", paperId] });
      });
      source.addEventListener("detail_ready", (e) => {
        const data = parse(e as MessageEvent<string>);
        recovered();
        if (data.part === "edge") {
          void qc.invalidateQueries({ queryKey: keys.edge(paperId, String(data.link_id)) });
        } else if (data.part === "code" || data.part === "example" || data.part === "graph") {
          void qc.invalidateQueries({ queryKey: [data.part, paperId, String(data.concept_id)] });
          void qc.invalidateQueries({ queryKey: keys.concept(paperId, String(data.concept_id)) });
          if (data.part === "code") {
            // Grounding changes the node's shape: refresh the layers on screen.
            void qc.invalidateQueries({ queryKey: ["layer", paperId], refetchType: "active" });
          }
        } else {
          void qc.invalidateQueries({ queryKey: ["explanation", paperId, String(data.concept_id)] });
          void qc.invalidateQueries({ queryKey: keys.concept(paperId, String(data.concept_id)) });
        }
        void qc.invalidateQueries({ queryKey: keys.budget() });
      });
      source.addEventListener("route_ready", (e) => {
        const data = parse(e as MessageEvent<string>);
        recovered();
        void qc.invalidateQueries({ queryKey: keys.route(paperId, String(data.route_id)) });
      });
      source.addEventListener("finding_added", () => {
        void qc.invalidateQueries({ queryKey: keys.findings(paperId) });
        void qc.invalidateQueries({ queryKey: ["layer", paperId] });
        void qc.invalidateQueries({ queryKey: ["concept", paperId] });
      });
      source.addEventListener("faq_ready", () => {
        void qc.invalidateQueries({ queryKey: keys.faq(paperId) });
      });
      source.addEventListener("rate_limited", (e) => {
        const data = parse(e as MessageEvent<string>);
        const wait = Number(data.wait_seconds ?? 10);
        setState((s) => ({ ...s, rateLimitedUntil: Date.now() + wait * 1000 }));
      });
      source.addEventListener("error", (e) => {
        const msg = (e as MessageEvent<string>).data;
        if (typeof msg === "string" && msg) {
          void qc.invalidateQueries({ queryKey: keys.paper(paperId) }); // a job failed: show why
          const data = parse(e as MessageEvent<string>);
          // "RuntimeError: ..." -> "...": the class name means nothing to a reader.
          const text = String(data.message ?? "Something went wrong.").replace(/^[A-Za-z]+: /, "");
          setState((s) => ({ ...s, lastError: text }));
        } else if (source.readyState === EventSource.CLOSED && !closed) {
          // The browser gives up after an HTTP error (server restart, proxy): reopen ourselves,
          // and refetch what an event might have announced while we were away.
          retryTimer = setTimeout(() => {
            connect();
            void qc.invalidateQueries({ queryKey: keys.paper(paperId) });
            void qc.invalidateQueries({ queryKey: ["layer", paperId], refetchType: "active" });
          }, backoff);
          backoff = Math.min(backoff * 2, 30_000);
        }
      });
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retryTimer);
      source.close();
    };
  }, [paperId, qc]);

  return state;
}
