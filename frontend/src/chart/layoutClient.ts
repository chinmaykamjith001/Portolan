// Layout on demand with a per-(paper, layer) position cache, so revisiting a layer looks identical.

import { useEffect, useState } from "react";

import type { LayerPayload } from "../api/types";
import { computeLayout, layoutInputFor, layoutKey, ringLayout, type ElkLike, type LayoutInput, type Positions } from "./layout";

const cache = new Map<string, Positions>();
const inflight = new Map<string, Promise<Positions>>();

let elk: Promise<ElkLike> | null = null;

/**
 * ELK runs in its own Web Worker (elk-worker.min.js, driven by elk-api). Without Worker support
 * (tests, old browsers) the bundled build runs on the main thread instead.
 */
function getElk(): Promise<ElkLike> {
  elk ??=
    typeof Worker === "undefined"
      ? import("elkjs/lib/elk.bundled.js").then((m) => new m.default() as unknown as ElkLike)
      : Promise.all([import("elkjs/lib/elk-api.js"), import("elkjs/lib/elk-worker.min.js?url")]).then(
          ([api, url]) => new api.default({ workerUrl: url.default }) as unknown as ElkLike,
        );
  return elk;
}

function run(input: LayoutInput): Promise<Positions> {
  return computeLayout(input, getElk);
}

export function cachedLayout(key: string): Positions | undefined {
  return cache.get(key);
}

export function layoutFor(paperId: string, payload: LayerPayload): { key: string; promise: Promise<Positions> } {
  const input = layoutInputFor(payload);
  const key = layoutKey(paperId, payload.layer.id, input);
  const hit = cache.get(key);
  if (hit) return { key, promise: Promise.resolve(hit) };
  let p = inflight.get(key);
  if (!p) {
    p = run(input)
      .catch(() => ringLayout(input)) // a layout failure must never leave the canvas empty
      .then((pos) => {
        cache.set(key, pos);
        inflight.delete(key);
        return pos;
      });
    inflight.set(key, p);
  }
  return { key, promise: p };
}

/** Positions for a payload, or null while the layout is computing. */
export function useLayout(paperId: string | null, payload: LayerPayload | null | undefined): Positions | null {
  const job = paperId && payload ? layoutFor(paperId, payload) : null;
  const key = job?.key ?? null;
  const [state, setState] = useState<{ key: string; pos: Positions } | null>(null);
  useEffect(() => {
    if (!job) return;
    let alive = true;
    void job.promise.then((pos) => {
      if (alive) setState({ key: job.key, pos });
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` identifies the job
  }, [key]);
  if (key === null) return null;
  return cache.get(key) ?? (state?.key === key ? state.pos : null);
}
