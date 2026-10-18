/**
 * Route view (spec §13.5, §13.10).
 *
 * Exports:
 * - default `RouteView`: the data-connected view. Reads `paperId` and `routePath` from the UI store and
 *   loads routes with `useRoute`. App.tsx lazy-imports it.
 * - `RouteCanvas`: the same canvas for a given `Route`, with no fetching of its own (it still needs a
 *   TanStack `QueryClientProvider`, a sized parent, and `routePath`/`stopId` in the UI store).
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

import { useRoute } from "../api/hooks";
import type { Concept, Route } from "../api/types";
import { Legend } from "../chart/Legend";
import { Button } from "../design/primitives";
import { useUI } from "../state/store";
import { cachedRoute, MAIN, routeQuery, useStopConcepts } from "./data";
import { layoutRoute, STOP_H, STOP_W, type RouteLayout } from "./layout";
import { RoadEdge, type RoadEdgeType } from "./RoadEdge";
import { RouteBreadcrumbs } from "./RouteBreadcrumbs";
import styles from "./route.module.css";
import { SegmentMarker, type MarkerNodeType } from "./SegmentMarker";
import { describeStop, StopNode, type StopNodeData, type StopNodeType } from "./StopNode";

const T = 300; // one jump phase, ms (spec §13.4: ≈300 out + ≈300 in)
const STAGGER_CAP = 150; // ms; the whole jump stays under ~650 ms
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 2;
const FIT_MAX_ZOOM = 1.1;
const FIT_PADDING = 0.1;
const READ_ZOOM = 0.7; // never pan around at a zoom where the text is unreadable
const DRAWER_PAD = 420; // the open drawer covers this much of the right edge

type RouteNode = StopNodeType | MarkerNodeType;

const nodeTypes = { stop: StopNode, marker: SegmentMarker };
const edgeTypes = { road: RoadEdge };

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

interface Shown {
  routeId: string;
  route: Route;
  layout: RouteLayout;
}

function fitAll(shown: Shown, w: number, h: number): Viewport {
  return getViewportForBounds(shown.layout.bounds, Math.max(w, 1), Math.max(h, 1), MIN_ZOOM, FIT_MAX_ZOOM, FIT_PADDING);
}

function centeredOn(x: number, y: number, zoom: number, w: number, h: number, padRight = 0): Viewport {
  return { x: (w - padRight) / 2 - x * zoom, y: h / 2 - y * zoom, zoom };
}

/** Where a route starts: the whole road if it is short enough to read, else its width, near `focusId`. */
export function startViewport(shown: Shown, w: number, h: number, focusId?: string | null): Viewport {
  const fit = fitAll(shown, w, h);
  if (fit.zoom >= 0.6) return fit;
  const b = shown.layout.bounds;
  const zoom = Math.min(1, Math.max(0.6, (Math.max(w, 1) * (1 - 2 * 0.08)) / b.width));
  const p = shown.layout.stops.find((s) => s.id === focusId) ?? shown.layout.stops[0];
  return centeredOn(b.x + b.width / 2, (p?.y ?? 0) + h * 0.12 / zoom, zoom, w, h);
}

/** Stagger delays for the jump-in fade: along the road, ≤ 25 ms apart, capped. */
export function enterDelays(ids: string[]): Record<string, number> {
  const step = Math.min(25, STAGGER_CAP / Math.max(1, ids.length - 1));
  return Object.fromEntries(ids.map((id, i) => [id, Math.round(i * step)]));
}

export function buildNodes(
  shown: Shown,
  concepts: Record<string, Concept>,
  enter: Record<string, number> | null,
  subCount: (stopId: string) => number | null,
): RouteNode[] {
  const { route, layout } = shown;
  const number = new Map(route.stops.map((s, i) => [s.id, i + 1]));
  const stopNodes = layout.stops.flatMap((p): StopNodeType[] => {
    const stop = route.stops[p.index];
    if (!stop) return [];
    const parts: string[] = [];
    if (stop.first_stop_id && number.has(stop.first_stop_id)) {
      parts.push(`First mentioned at stop ${number.get(stop.first_stop_id)}`);
    }
    const again = stop.seen_again_at.flatMap((id) => (number.has(id) ? [number.get(id)] : []));
    if (again.length > 0) parts.push(`Seen again at stop ${again.join(", ")}`);
    const data: StopNodeData = {
      stop,
      number: p.index + 1,
      concept: stop.concept_id ? (concepts[stop.concept_id] ?? null) : null,
      subCount: stop.sub_route_id ? subCount(stop.id) : null,
      recurrence: parts.join(" · ") || null,
      enterDelay: enter ? (enter[stop.id] ?? 0) : null,
    };
    return [
      {
        id: stop.id,
        type: "stop",
        position: { x: p.x - STOP_W / 2, y: p.y - STOP_H / 2 },
        width: STOP_W,
        height: STOP_H,
        draggable: false,
        selectable: false,
        connectable: false,
        ariaLabel: describeStop(data),
        data,
      },
    ];
  });
  const markers = layout.markers.map(
    (m): MarkerNodeType => ({
      id: `marker:${m.stopId}`,
      type: "marker",
      position: { x: m.x, y: m.y },
      width: 190,
      height: 40,
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
      ariaLabel: `Section ${m.text}`,
      data: { text: m.text, side: m.side },
    }),
  );
  return [...markers, ...stopNodes];
}

export function buildEdges(shown: Shown, currentIndex: number): RoadEdgeType[] {
  return shown.layout.connectors.map((c): RoadEdgeType => {
    const from = shown.route.stops[c.fromIndex];
    return {
      id: c.id,
      source: c.fromId,
      target: c.toId,
      sourceHandle: c.side,
      targetHandle: "top",
      type: "road",
      selectable: false,
      focusable: false,
      data: {
        path: c.path,
        arrow: c.arrow,
        mid: c.mid,
        label: from?.transition_to_next ?? "",
        traveled: currentIndex >= c.toIndex,
      },
    };
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
  /** The route the user wants to see ("main" or a stop id). */
  routeId: string;
  /** Its data, or null while it is being laid out. */
  route: Route | null;
  concepts?: Record<string, Concept>;
  /** A quiet status line for the floating bar, or an error with a next step. */
  status?: ReactNode;
  /** Centered line while there is nothing to draw ("Laying out the road…"). */
  emptyText?: string | null;
  /** Reveal this stop (from "Find on Route"); `onRevealed` is called once it is on screen. */
  reveal?: string | null;
  onRevealed?: () => void;
}

function Scene({ paperId, routeId, route, concepts = {}, status, emptyText, reveal = null, onRevealed }: CanvasProps) {
  const rf = useReactFlow<RouteNode, RoadEdgeType>();
  const qc = useQueryClient();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const depth = useUI((s) => s.routePath.length);
  const stopId = useUI((s) => s.stopId);
  const selectStop = useUI((s) => s.selectStop);
  const routeJumpIn = useUI((s) => s.routeJumpIn);

  const layout = useMemo(() => (route ? layoutRoute(route.stops) : null), [route]);
  const [shown, setShown] = useState<Shown | null>(null);
  const [outDone, setOutDone] = useState<string | null>(null);
  const [opacity, setOpacity] = useState(1);
  const [enter, setEnter] = useState<Record<string, number> | null>(null);
  const token = useRef(0);
  const prevDepth = useRef(depth);
  const direction = useRef<"in" | "back">("in");
  const wrapper = useRef<HTMLDivElement>(null);
  const size = useRef({ w: width, h: height });
  size.current = { w: width, h: height };

  // Phase 1 — leave the current route (zoom toward the stop we jump from / shrink away), fading it out.
  useEffect(() => {
    const t = ++token.current;
    direction.current = depth >= prevDepth.current ? "in" : "back";
    prevDepth.current = depth;
    setEnter(null);
    if (!shown || shown.routeId === routeId) {
      setOutDone(routeId);
      if (shown && opacity !== 1) {
        setOpacity(1);
        void rf.setViewport(startViewport(shown, size.current.w, size.current.h), { duration: prefersReducedMotion() ? 0 : T });
      }
      return;
    }
    setOutDone(null);
    if (prefersReducedMotion()) {
      setOutDone(routeId);
      return;
    }
    const vp = rf.getViewport();
    const from = direction.current === "in" ? shown.layout.stops.find((s) => s.id === routeId) : undefined;
    if (from) {
      void rf.setCenter(from.x, from.y, { zoom: Math.min(vp.zoom * 3, MAX_ZOOM), duration: T });
    } else {
      void rf.zoomTo(Math.max(vp.zoom * 0.5, MIN_ZOOM), { duration: T });
    }
    setOpacity(0);
    const timer = window.setTimeout(() => {
      if (token.current === t) setOutDone(routeId);
    }, T);
    return () => window.clearTimeout(timer);
    // Only a change of target starts a transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeId]);

  // Phase 2 — once the old route is gone and the new one is laid out, swap and grow it into view.
  useEffect(() => {
    if (outDone !== routeId) return;
    if (!route || !layout) {
      if (shown && shown.routeId !== routeId) setOpacity(0.3); // still laying out: keep the old road faintly
      return;
    }
    if (shown && shown.routeId === routeId) {
      if (shown.route !== route) setShown({ routeId, route, layout }); // same road, fresher data
      return;
    }
    const next: Shown = { routeId, route, layout };
    const { w, h } = size.current;
    const cameFrom = useUI.getState().cameFromStop;
    const fit = startViewport(next, w, h, direction.current === "back" ? cameFrom : null);
    const t = token.current;
    if (!shown || prefersReducedMotion()) {
      setShown(next);
      setOpacity(1);
      void rf.setViewport(fit);
      return;
    }
    let start: Viewport;
    if (direction.current === "in") {
      const b = layout.bounds;
      start = centeredOn(b.x + b.width / 2, b.y + Math.min(b.height / 2, h / fit.zoom / 2), fit.zoom * 0.5, w, h);
      setEnter(enterDelays(route.stops.map((s) => s.id)));
    } else {
      const p = layout.stops.find((s) => s.id === cameFrom) ?? layout.stops[0] ?? { x: 0, y: 0 };
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
  }, [outDone, routeId, route, layout]);

  // First size of the pane (it is 0×0 until React Flow measures it): place what we have.
  const measured = width > 0 && height > 0;
  useEffect(() => {
    if (measured && shown) void rf.setViewport(startViewport(shown, size.current.w, size.current.h, useUI.getState().stopId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measured]);

  const stops = shown?.route.stops;
  const currentIndex = stops && stopId ? stops.findIndex((s) => s.id === stopId) : -1;

  // After Back, the stop we came out of is the current one (spec §13.5).
  useEffect(() => {
    if (!shown || shown.routeId !== routeId) return;
    const from = useUI.getState().cameFromStop;
    if (!from) return;
    const stop = shown.route.stops.find((s) => s.id === from);
    if (stop) selectStop(stop, false);
    useUI.getState().clearCameFromStop();
  }, [shown, routeId, selectStop]);

  const nodes = useMemo(
    () =>
      shown
        ? buildNodes(shown, concepts, enter, (id) => cachedRoute(qc, paperId, id)?.stops.length ?? null)
        : [],
    [shown, concepts, enter, qc, paperId],
  );
  const edges = useMemo(() => (shown ? buildEdges(shown, currentIndex) : []), [shown, currentIndex]);

  const fitView = useCallback(() => {
    if (shown) void rf.setViewport(fitAll(shown, size.current.w, size.current.h), { duration: prefersReducedMotion() ? 0 : 200 });
  }, [rf, shown]);

  /** Pan only if the stop is out of sight (or under the drawer), never to a zoom you can't read. */
  const ensureVisible = useCallback(
    (id: string, minZoom = READ_ZOOM) => {
      const p = shown?.layout.stops.find((s) => s.id === id);
      if (!p) return;
      const { w, h } = size.current;
      const vp = rf.getViewport();
      const pad = useUI.getState().drawerOpen ? DRAWER_PAD : 0;
      const sx = p.x * vp.zoom + vp.x;
      const sy = p.y * vp.zoom + vp.y;
      const mx = (STOP_W / 2) * vp.zoom + 24;
      const my = (STOP_H / 2) * vp.zoom + 24;
      const inView = sx - mx >= 0 && sx + mx <= w - pad && sy - my >= 0 && sy + my <= h;
      if (inView && vp.zoom >= minZoom) return;
      void rf.setViewport(centeredOn(p.x, p.y, Math.max(vp.zoom, minZoom), w, h, pad), {
        duration: prefersReducedMotion() ? 0 : 250,
      });
    },
    [rf, shown],
  );

  const prefetchSub = useCallback(
    (id: string) => {
      const stop = shown?.route.stops.find((s) => s.id === id);
      if (stop?.sub_route_id) void qc.prefetchQuery(routeQuery(paperId, stop.id));
    },
    [qc, paperId, shown],
  );

  const goTo = useCallback(
    (index: number) => {
      const stop = shown?.route.stops[index];
      if (!stop) return;
      selectStop(stop, useUI.getState().drawerOpen);
      ensureVisible(stop.id);
      prefetchSub(stop.id);
    },
    [shown, selectStop, ensureVisible, prefetchSub],
  );
  const next = useCallback(() => goTo(Math.min((shown?.route.stops.length ?? 1) - 1, currentIndex + 1)), [goTo, shown, currentIndex]);
  const previous = useCallback(() => goTo(Math.max(0, currentIndex - 1)), [goTo, currentIndex]);

  const jumpable = useCallback(
    (id: string) => shown?.route.stops.find((s) => s.id === id)?.sub_route_id != null,
    [shown],
  );

  // Show on Route: reveal a stop once its road is on screen.
  useEffect(() => {
    if (!reveal || !shown || shown.routeId !== routeId) return;
    if (!shown.layout.stops.some((s) => s.id === reveal)) {
      onRevealed?.();
      return;
    }
    const timer = window.setTimeout(
      () => {
        ensureVisible(reveal, 0.9);
        onRevealed?.();
      },
      prefersReducedMotion() ? 0 : T + 60,
    );
    return () => window.clearTimeout(timer);
  }, [reveal, shown, routeId, ensureVisible, onRevealed]);

  // Keyboard (spec §13.10). Esc is global (App.tsx → store.escape).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || useUI.getState().dialog) return;
      const el = document.activeElement;
      if (isTyping(el)) return;
      const onBody = el === null || el === document.body;
      const inCanvas =
        el instanceof Element &&
        wrapper.current?.contains(el) === true &&
        el.closest("button, a, [role='dialog']") === null;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        previous();
      } else if (e.key === "f") {
        e.preventDefault();
        fitView();
      } else if ((e.key === "Enter" || e.key === " ") && (onBody || inCanvas)) {
        const sel = useUI.getState().stopId;
        if (sel && jumpable(sel)) {
          e.preventDefault();
          routeJumpIn(sel);
        }
      } else if (e.key === "Tab" && inCanvas && shown) {
        const ids = shown.route.stops.map((s) => s.id);
        if (ids.length === 0) return;
        e.preventDefault();
        const i = stopId ? ids.indexOf(stopId) : -1;
        const target = ids[(i + (e.shiftKey ? -1 : 1) + ids.length) % ids.length] ?? ids[0];
        if (target === undefined) return;
        goTo(ids.indexOf(target));
        wrapper.current?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(target)}"]`)?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, previous, fitView, jumpable, routeJumpIn, goTo, shown, stopId]);

  const onNodeClick: NodeMouseHandler<RouteNode> = useCallback(
    (e, node) => {
      if (node.type !== "stop" || e.detail >= 2) return; // the click pair of a double-click
      const stop = shown?.route.stops.find((s) => s.id === node.id);
      if (!stop) return;
      selectStop(stop, true);
      prefetchSub(stop.id);
    },
    [shown, selectStop, prefetchSub],
  );
  const onNodeDoubleClick: NodeMouseHandler<RouteNode> = useCallback(
    (_e, node) => {
      if (node.type === "stop" && jumpable(node.id)) routeJumpIn(node.id);
    },
    [jumpable, routeJumpIn],
  );

  const style = { "--layer-opacity": opacity, "--layer-fade": `${T}ms` } as CSSProperties;
  const total = shown?.route.stops.length ?? 0;

  return (
    <div ref={wrapper} className={styles.canvas} style={style}>
      <ReactFlow<RouteNode, RoadEdgeType>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
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
        aria-label="Route"
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1.4} color="var(--grid)" />
        <Panel position="top-left">
          <RouteBreadcrumbs paperId={paperId} status={status ?? null} />
        </Panel>
        <Panel position="bottom-left">
          <div className={styles.controls}>
            <Button onClick={previous} disabled={total === 0 || currentIndex <= 0} aria-label="Previous stop">
              Previous
            </Button>
            <Button onClick={next} disabled={total === 0 || currentIndex >= total - 1} aria-label="Next stop">
              Next
            </Button>
            {total > 0 && currentIndex >= 0 && (
              <span className={styles.position} aria-live="polite">
                Stop {currentIndex + 1} of {total}
              </span>
            )}
            <Button onClick={fitView}>Fit view</Button>
          </div>
        </Panel>
        <Panel position="bottom-right">
          <Legend />
        </Panel>
      </ReactFlow>
      {(!shown || total === 0) && emptyText && <p className={styles.empty}>{emptyText}</p>}
    </div>
  );
}

export function RouteCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <Scene {...props} />
    </ReactFlowProvider>
  );
}

export default function RouteView() {
  const paperId = useUI((s) => s.paperId);
  const routeId = useUI((s) => s.routePath[s.routePath.length - 1] ?? MAIN);
  const pendingFocus = useUI((s) => s.pendingFocus);
  const clearFocus = useUI((s) => s.clearFocus);
  const selectStop = useUI((s) => s.selectStop);
  const stopId = useUI((s) => s.stopId);
  const query = useRoute(paperId, routeId);
  const main = useRoute(paperId, MAIN);
  const concepts = useStopConcepts(paperId, query.data ?? null);
  const [reveal, setReveal] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // "Find on Route": go to the main route and make the concept's first stop current.
  const wanted = pendingFocus?.view === "route" ? pendingFocus.conceptId : null;
  useEffect(() => {
    if (wanted === null || !main.data) return;
    if (useUI.getState().routePath.length > 0) useUI.setState({ routePath: [] });
    const stop = main.data.stops.find((s) => s.concept_id === wanted);
    if (stop) {
      selectStop(stop, false);
      setReveal(stop.id);
      setNote(null);
    } else {
      setNote("This idea isn't on the main route.");
    }
    clearFocus();
  }, [wanted, main.data, selectStop, clearFocus]);

  // The note goes away once the reader moves to another stop (not when the view first opens on one).
  const lastStop = useRef(stopId);
  useEffect(() => {
    if (stopId === lastStop.current) return;
    lastStop.current = stopId;
    setNote(null);
  }, [stopId]);

  const done = useCallback(() => setReveal(null), []);

  if (!paperId) return null;

  let status: ReactNode = note;
  let emptyText: string | null = null;
  if (query.isError) {
    status = (
      <>
        Couldn&apos;t lay out the road.{" "}
        <button type="button" className={styles.textButton} onClick={() => void query.refetch()}>
          Try again
        </button>
      </>
    );
  } else if (!query.data) {
    status = "Laying out the road…";
    emptyText = "Laying out the road…";
  } else if (query.data.stops.length === 0) {
    status = "There are no stops on this road yet.";
    emptyText = "There are no stops on this road yet.";
  }

  return (
    <RouteCanvas
      paperId={paperId}
      routeId={routeId}
      route={query.data ?? null}
      concepts={concepts}
      status={status}
      emptyText={emptyText}
      reveal={reveal}
      onRevealed={done}
    />
  );
}
