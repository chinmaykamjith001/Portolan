/**
 * Chart view (spec §13.2–13.4, §13.7).
 *
 * Exports:
 * - default `ChartView`: the data-connected view. Reads `paperId` and `path` from the UI store and
 *   loads layers with `useLayer`. App.tsx lazy-imports it.
 * - `ChartCanvas`: the same canvas for a given `LayerPayload`, with no fetching of its own. Use it with
 *   `syntheticLayer(n)` from `./synthetic` for performance checks, e.g.
 *   `<ChartCanvas paperId="synthetic" layerId="master" payload={syntheticLayer(300)} />`.
 *   It must render inside a TanStack `QueryClientProvider` (breadcrumbs and the edge popover read the
 *   query cache) and inside a sized box (it fills its parent).
 */

import "@xyflow/react/dist/base.css";

import { useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  getViewportForBounds,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type NodeMouseHandler,
  type Viewport,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { useLayer } from "../api/hooks";
import type { LayerPayload } from "../api/types";
import { Button } from "../design/primitives";
import { currentLayerId, MASTER, useUI } from "../state/store";
import { Breadcrumbs } from "./Breadcrumbs";
import styles from "./chart.module.css";
import { ConceptEdge, type ConceptEdgeType } from "./ConceptEdge";
import { canJumpInto, ConceptNode, describeConcept, type ConceptNodeType } from "./ConceptNode";
import { cachedLayer, layerQuery } from "./data";
import { EdgePopover } from "./EdgePopover";
import { layoutInputFor, sizeOf, type Positions } from "./layout";
import { useLayout } from "./layoutClient";
import { Legend } from "./Legend";

const T = 300; // one jump phase, ms (spec §13.4: ≈300 out + ≈300 in)
const STAGGER_CAP = 150; // ms; total jump stays under ~650 ms
const HOVER_PREFETCH_MS = 300;
const MIN_ZOOM = 0.04;
const MAX_ZOOM = 4;
const FIT_MAX_ZOOM = 1.4;
const FIT_PADDING = 0.12;

const nodeTypes = { concept: ConceptNode };
const edgeTypes = { concept: ConceptEdge };

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

interface Shown {
  layerId: string;
  payload: LayerPayload;
  positions: Positions;
}

function bounds(shown: Shown) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const center = shown.payload.layer.center;
  for (const [id, p] of Object.entries(shown.positions)) {
    const h = sizeOf(id === center) / 2;
    minX = Math.min(minX, p.x - h);
    minY = Math.min(minY, p.y - h);
    maxX = Math.max(maxX, p.x + h);
    maxY = Math.max(maxY, p.y + h);
  }
  if (minX === Infinity) return { x: 0, y: 0, width: 1, height: 1 };
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function fitFor(shown: Shown, w: number, h: number): Viewport {
  return getViewportForBounds(bounds(shown), Math.max(w, 1), Math.max(h, 1), MIN_ZOOM, FIT_MAX_ZOOM, FIT_PADDING);
}

function centeredOn(x: number, y: number, zoom: number, w: number, h: number): Viewport {
  return { x: w / 2 - x * zoom, y: h / 2 - y * zoom, zoom };
}

/** Stagger delays for the jump-in fade: nearest to the center first, ≤ 25 ms apart, capped. */
export function enterDelays(positions: Positions): Record<string, number> {
  const ids = Object.keys(positions).sort((a, b) => {
    const pa = positions[a] ?? { x: 0, y: 0 };
    const pb = positions[b] ?? { x: 0, y: 0 };
    return Math.hypot(pa.x, pa.y) - Math.hypot(pb.x, pb.y) || a.localeCompare(b);
  });
  const step = Math.min(25, STAGGER_CAP / Math.max(1, ids.length - 1));
  return Object.fromEntries(ids.map((id, i) => [id, Math.round(i * step)]));
}

export function buildNodes(
  shown: Shown,
  enter: Record<string, number> | null,
  childCount: (id: string) => number | null,
  issuesInside: Record<string, number> = {},
): ConceptNodeType[] {
  const { payload, positions } = shown;
  const center = payload.layer.center;
  return layoutInputFor(payload).nodes.flatMap((n) => {
    const c = payload.concepts[n.id];
    if (!c) return [];
    const isCenter = n.id === center;
    const p = positions[n.id] ?? { x: 0, y: 0 };
    const node: ConceptNodeType = {
      id: n.id,
      type: "concept",
      position: { x: p.x - n.size / 2, y: p.y - n.size / 2 },
      width: n.size,
      height: n.size,
      draggable: false,
      selectable: false,
      connectable: false,
      ariaLabel: describeConcept(c, isCenter, canJumpInto(c, isCenter)),
      data: {
        concept: c,
        center: isCenter,
        size: n.size,
        childCount: c.has_layer ? childCount(n.id) : null,
        issuesInside: issuesInside[n.id] ?? 0,
        enterDelay: enter ? (enter[n.id] ?? 0) : null,
      },
    };
    return [node];
  });
}

export function buildEdges(payload: LayerPayload): ConceptEdgeType[] {
  const { layer, concepts } = payload;
  return layer.links.flatMap((l) => {
    const s = concepts[l.source];
    const t = concepts[l.target];
    if (!s || !t || l.source === l.target) return [];
    const edge: ConceptEdgeType = {
      id: l.id,
      source: l.source,
      target: l.target,
      type: "concept",
      selectable: false,
      focusable: false,
      ariaLabel: `${s.title} ${l.label} ${t.title}`,
      data: {
        label: l.label,
        sourceShape: s.grounding,
        sourceSize: sizeOf(l.source === layer.center),
        targetShape: t.grounding,
        targetSize: sizeOf(l.target === layer.center),
      },
    };
    return [edge];
  });
}

function isTyping(el: Element | null): boolean {
  return (
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement ||
    (el instanceof HTMLElement && el.isContentEditable)
  );
}

interface CanvasProps {
  paperId: string;
  /** The layer the user wants to see (the store's current layer). */
  layerId: string;
  /** Its payload, or null while it's being generated. */
  payload: LayerPayload | null;
  /** A quiet status line for the floating bar ("Building this layer…"), or an error with a next step. */
  status?: ReactNode;
  /** Called after hovering a concept for 300 ms (prefetch its layer, spec §13.2). */
  onHoverConcept?: (conceptId: string) => void;
  /** Findings inside each concept's sub-map (M8). */
  issuesInside?: Record<string, number>;
}

function Scene({ paperId, layerId, payload, status, onHoverConcept, issuesInside }: CanvasProps) {
  const rf = useReactFlow<ConceptNodeType, ConceptEdgeType>();
  const qc = useQueryClient();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const pathLength = useUI((s) => s.path.length);
  const select = useUI((s) => s.select);
  const jumpIn = useUI((s) => s.jumpIn);
  const positions = useLayout(paperId, payload);

  const [shown, setShown] = useState<Shown | null>(null);
  const [outDone, setOutDone] = useState<string | null>(null);
  const [opacity, setOpacity] = useState(1);
  const [enter, setEnter] = useState<Record<string, number> | null>(null);
  const token = useRef(0);
  const prevDepth = useRef(pathLength);
  const direction = useRef<"in" | "back">("in");
  const wrapper = useRef<HTMLDivElement>(null);
  const size = useRef({ w: width, h: height });
  size.current = { w: width, h: height };

  // Phase 1 — leave the current layer (zoom toward the target / shrink away) while fading it out.
  useEffect(() => {
    const t = ++token.current;
    direction.current = pathLength >= prevDepth.current ? "in" : "back";
    prevDepth.current = pathLength;
    setEnter(null);
    if (!shown || shown.layerId === layerId) {
      setOutDone(layerId);
      if (shown && opacity !== 1) {
        // Came straight back to the layer we were leaving: restore it.
        setOpacity(1);
        void rf.setViewport(fitFor(shown, size.current.w, size.current.h), { duration: prefersReducedMotion() ? 0 : T });
      }
      return;
    }
    setOutDone(null);
    if (prefersReducedMotion()) {
      setOutDone(layerId);
      return;
    }
    const vp = rf.getViewport();
    const target = shown.positions[layerId];
    if (direction.current === "in" && target) {
      void rf.setCenter(target.x, target.y, { zoom: Math.min(vp.zoom * 3, MAX_ZOOM), duration: T });
    } else {
      void rf.zoomTo(Math.max(vp.zoom * 0.5, MIN_ZOOM), { duration: T });
    }
    setOpacity(0);
    const timer = window.setTimeout(() => {
      if (token.current === t) setOutDone(layerId);
    }, T);
    return () => window.clearTimeout(timer);
    // Only a change of target starts a transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerId]);

  // Phase 2 — once the old layer is gone and the new one is laid out, swap and grow it into view.
  useEffect(() => {
    if (outDone !== layerId) return;
    if (!payload || !positions) {
      if (shown && shown.layerId !== layerId) setOpacity(0.3); // still building: keep the old map faintly
      return;
    }
    if (shown && shown.layerId === layerId) {
      if (shown.payload !== payload || shown.positions !== positions) {
        setShown({ layerId, payload, positions }); // same layer, fresher data (e.g. gists arrived)
      }
      return;
    }
    const next: Shown = { layerId, payload, positions };
    const { w, h } = size.current;
    const fit = fitFor(next, w, h);
    const t = token.current;
    if (!shown || prefersReducedMotion()) {
      setShown(next);
      setOpacity(1);
      void rf.setViewport(fit);
      return;
    }
    let start: Viewport;
    if (direction.current === "in") {
      const b = bounds(next);
      start = centeredOn(b.x + b.width / 2, b.y + b.height / 2, fit.zoom * 0.5, w, h);
      setEnter(enterDelays(positions));
    } else {
      const from = useUI.getState().cameFrom;
      const p = (from && positions[from]) || { x: 0, y: 0 };
      start = centeredOn(p.x, p.y, Math.min(fit.zoom * 2.2, MAX_ZOOM), w, h);
    }
    setShown(next);
    setOpacity(0);
    void rf.setViewport(start);
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => {
        if (token.current !== t) return;
        setOpacity(1);
        void rf.setViewport(fit, { duration: T });
      });
    });
    const clear = window.setTimeout(() => {
      if (token.current === t) setEnter(null);
    }, T + STAGGER_CAP + 50);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(clear);
    };
    // `shown` is read, not watched: the swap itself must not re-trigger this effect's animation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outDone, layerId, payload, positions]);

  // First size of the pane (it is 0×0 until React Flow measures it): fit what we have.
  const measured = width > 0 && height > 0;
  useEffect(() => {
    if (measured && shown) void rf.setViewport(fitFor(shown, size.current.w, size.current.h));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measured]);

  const nodes = useMemo(
    () =>
      shown
        ? buildNodes(shown, enter, (id) => cachedLayer(qc, paperId, id)?.layer.concepts.length ?? null, issuesInside)
        : [],
    [shown, enter, qc, paperId, issuesInside],
  );
  const edges = useMemo(() => (shown ? buildEdges(shown.payload) : []), [shown]);

  const fitView = useCallback(() => {
    if (shown) {
      void rf.setViewport(fitFor(shown, size.current.w, size.current.h), {
        duration: prefersReducedMotion() ? 0 : 200,
      });
    }
  }, [rf, shown]);

  const jumpable = useCallback(
    (id: string) => {
      const c = shown?.payload.concepts[id];
      return c !== undefined && canJumpInto(c, id === shown?.payload.layer.center);
    },
    [shown],
  );

  // Keyboard (spec §13.10). Esc is global (App.tsx → store.escape).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const el = document.activeElement;
      if (isTyping(el)) return;
      const onBody = el === null || el === document.body;
      const inCanvas =
        el instanceof Element &&
        wrapper.current?.contains(el) === true &&
        el.closest("button, a, [role='dialog']") === null;
      if (e.key === "f") {
        e.preventDefault();
        fitView();
      } else if ((e.key === "Enter" || e.key === " ") && (onBody || inCanvas)) {
        const sel = useUI.getState().selectedId;
        if (sel && jumpable(sel)) {
          e.preventDefault();
          jumpIn(sel);
        }
      } else if (e.key === "Tab" && inCanvas && shown) {
        const ids = nodes.map((n) => n.id);
        if (ids.length === 0) return;
        e.preventDefault();
        const st = useUI.getState();
        const i = st.selectedId ? ids.indexOf(st.selectedId) : -1;
        const next = ids[(i + (e.shiftKey ? -1 : 1) + ids.length) % ids.length] ?? ids[0];
        if (!next) return;
        select(next, st.drawerOpen);
        wrapper.current?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(next)}"]`)?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fitView, jumpable, jumpIn, select, nodes, shown]);

  const hover = useRef<number | undefined>(undefined);
  const onNodeMouseEnter: NodeMouseHandler<ConceptNodeType> = useCallback(
    (_e, node) => {
      window.clearTimeout(hover.current);
      if (!onHoverConcept || !jumpable(node.id)) return;
      hover.current = window.setTimeout(() => onHoverConcept(node.id), HOVER_PREFETCH_MS);
    },
    [onHoverConcept, jumpable],
  );
  const onNodeMouseLeave = useCallback(() => window.clearTimeout(hover.current), []);
  useEffect(() => () => window.clearTimeout(hover.current), []);

  const onNodeClick: NodeMouseHandler<ConceptNodeType> = useCallback((_e, node) => select(node.id), [select]);
  const onNodeDoubleClick: NodeMouseHandler<ConceptNodeType> = useCallback(
    (_e, node) => {
      if (jumpable(node.id)) jumpIn(node.id);
    },
    [jumpable, jumpIn],
  );

  const style = {
    "--layer-opacity": opacity,
    "--layer-fade": `${T}ms`,
  } as CSSProperties;

  return (
    <div ref={wrapper} className={styles.canvas} style={style}>
      <ReactFlow<ConceptNodeType, ConceptEdgeType>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        nodesFocusable
        edgesFocusable={false}
        panOnDrag
        panOnScroll
        zoomOnScroll={false}
        zoomOnPinch
        zoomOnDoubleClick={false}
        panActivationKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        deleteKeyCode={null}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        attributionPosition="bottom-center"
        aria-label="Concept map"
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.4} color="var(--grid)" />
        <Panel position="top-left">
          <Breadcrumbs paperId={paperId} status={status ?? null} />
        </Panel>
        <Panel position="bottom-left">
          <Button onClick={fitView}>Fit view</Button>
        </Panel>
        <Panel position="bottom-right">
          <Legend />
        </Panel>
        {shown && <EdgePopover paperId={paperId} payload={shown.payload} width={width} height={height} />}
      </ReactFlow>
    </div>
  );
}

export function ChartCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <Scene {...props} />
    </ReactFlowProvider>
  );
}

export default function ChartView() {
  const paperId = useUI((s) => s.paperId);
  const layerId = useUI(currentLayerId);
  const qc = useQueryClient();
  const query = useLayer(paperId, layerId);
  const prefetch = useCallback(
    (conceptId: string) => {
      if (paperId) void qc.prefetchQuery(layerQuery(paperId, conceptId));
    },
    [qc, paperId],
  );
  if (!paperId) return null;

  let status: ReactNode = null;
  if (query.isError) {
    status = (
      <>
        Couldn&apos;t load this part of the map.{" "}
        <button type="button" className={styles.textButton} onClick={() => void query.refetch()}>
          Try again
        </button>
      </>
    );
  } else if (!query.data) {
    status = layerId === MASTER ? "Building the map…" : "Building this layer…";
  }

  return (
    <ChartCanvas
      paperId={paperId}
      layerId={layerId}
      payload={query.data ?? null}
      status={status}
      onHoverConcept={prefetch}
    />
  );
}
