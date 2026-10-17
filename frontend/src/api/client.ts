// Typed client for the Portolan backend. 202 responses mean "being generated": callers get
// `{ pending: true }` and wait for an SSE event that invalidates the query.

import type {
  ApiErrorBody,
  Budget,
  ConceptPayload,
  CreatePaperResult,
  EdgeExplanation,
  Explanation,
  LayerPayload,
  PaperInfo,
  Angle,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export type Maybe<T> = { pending: true } | { pending: false; data: T };

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<Maybe<T>> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (res.status === 202) return { pending: true };
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? "Something went wrong.");
  }
  return { pending: false, data: body as T };
}

const enc = encodeURIComponent;

export const api = {
  createPaper: (input: string) =>
    request<CreatePaperResult>("/papers", { method: "POST", body: JSON.stringify({ input }) }),
  paper: (paperId: string) => request<PaperInfo>(`/papers/${enc(paperId)}`),
  master: (paperId: string) => request<LayerPayload>(`/papers/${enc(paperId)}/chart/master`),
  layer: (paperId: string, conceptId: string) =>
    request<LayerPayload>(`/papers/${enc(paperId)}/chart/layers/${enc(conceptId)}`),
  concept: (paperId: string, conceptId: string) =>
    request<ConceptPayload>(`/papers/${enc(paperId)}/concepts/${enc(conceptId)}`),
  explanation: (paperId: string, conceptId: string, angle: Angle = "default") =>
    request<Explanation>(
      `/papers/${enc(paperId)}/concepts/${enc(conceptId)}/explanation?angle=${angle}`,
    ),
  edge: (paperId: string, linkId: string) =>
    request<EdgeExplanation>(`/papers/${enc(paperId)}/links/${enc(linkId)}/explanation`),
  budget: () => request<Budget>("/budget"),
};

export function eventsUrl(paperId: string): string {
  return `${BASE}/papers/${enc(paperId)}/events`;
}

// TanStack Query keys, shared so SSE events can invalidate exactly what changed.
export const keys = {
  paper: (p: string) => ["paper", p] as const,
  layer: (p: string, layerId: string) => ["layer", p, layerId] as const,
  concept: (p: string, c: string) => ["concept", p, c] as const,
  explanation: (p: string, c: string, angle: Angle) => ["explanation", p, c, angle] as const,
  edge: (p: string, linkId: string) => ["edge", p, linkId] as const,
  budget: () => ["budget"] as const,
};
