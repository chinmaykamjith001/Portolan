import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import ELK from "elkjs/lib/elk.bundled.js";
import type { ReactNode } from "react";

import { keys } from "../src/api/client";
import type { Concept, LayerPayload } from "../src/api/types";
import { edgeGeometry } from "../src/chart/ConceptEdge";
import ChartView, { buildNodes, ChartCanvas, enterDelays } from "../src/chart/ChartView";
import { plainParagraphs } from "../src/chart/EdgePopover";
import {
  boundaryDistance,
  computeLayout,
  CENTER_SIZE,
  layoutInputFor,
  layoutKey,
  NODE_SIZE,
  type ElkLike,
  type Positions,
} from "../src/chart/layout";
import { syntheticLayer } from "../src/chart/synthetic";
import { useUI } from "../src/state/store";

// jsdom lacks what React Flow measures with; give it fixed sizes.
class RO {
  constructor(private cb: ResizeObserverCallback) {}
  observe(el: Element) {
    this.cb([{ target: el, contentRect: { width: 800, height: 600 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  globalThis.ResizeObserver = RO as unknown as typeof ResizeObserver;
  // Reduced motion: layer swaps are instant, so tests don't wait on animation timers.
  window.matchMedia = ((q: string) => ({
    matches: q.includes("reduce"),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  })) as unknown as typeof window.matchMedia;
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  globalThis.DOMMatrixReadOnly ??= class {
    m22 = 1;
  } as unknown as typeof DOMMatrixReadOnly;
});

beforeEach(() => {
  useUI.setState({ paperId: "p", view: "chart", path: [], selectedId: null, drawerOpen: false, edge: null, cameFrom: null });
});

const elk = () => Promise.resolve(new ELK() as unknown as ElkLike);

function concept(id: string, over: Partial<Concept> = {}): Concept {
  return {
    id,
    canonical_name: id,
    title: id.toUpperCase(),
    gist: `Gist of ${id}.`,
    grounding: "concept_only",
    is_first_principle: false,
    depth: 1,
    anchors: [],
    has_layer: false,
    gen_state: "detailed",
    issue_count_self: 0,
    ...over,
  };
}

function payload(layerId: string, center: Concept, others: Concept[]): LayerPayload {
  const concepts = Object.fromEntries([center, ...others].map((c) => [c.id, c]));
  return {
    layer: {
      id: layerId,
      paper_id: "p",
      center: center.id,
      concepts: others.map((c) => c.id),
      links: others.map((c) => ({
        id: `${layerId}:${center.id}->${c.id}`,
        layer_id: layerId,
        source: center.id,
        target: c.id,
        label: "uses",
        kind: "uses",
      })),
      gen_state: "detailed",
    },
    concepts,
  };
}

const MASTER_PAYLOAD = payload("master", concept("transformer", { depth: 0 }), [
  concept("attention", { grounding: "in_code", has_layer: true }),
  concept("softmax", { grounding: "in_library", is_first_principle: true }),
  concept("dropout", { grounding: "shown_by_running", issue_count_self: 1 }),
  concept("adam", { grounding: "unknown", gen_state: "generating", gist: "" }),
]);
const CHILD_PAYLOAD = payload("attention", concept("attention", { grounding: "in_code", has_layer: true }), [
  concept("query"),
  concept("key"),
]);

function withClient(ui: ReactNode, seed?: (qc: QueryClient) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed?.(qc);
  return render(
    <QueryClientProvider client={qc}>
      <div style={{ width: 800, height: 600 }}>{ui}</div>
    </QueryClientProvider>,
  );
}

function noOverlap(pos: Positions, center: string) {
  const ids = Object.keys(pos);
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      const a = ids[i] as string;
      const b = ids[j] as string;
      const pa = pos[a] as { x: number; y: number };
      const pb = pos[b] as { x: number; y: number };
      const min = ((a === center ? CENTER_SIZE : NODE_SIZE) + (b === center ? CENTER_SIZE : NODE_SIZE)) / 2;
      expect(Math.hypot(pa.x - pb.x, pa.y - pb.y)).toBeGreaterThanOrEqual(min - 1);
    }
  }
}

describe("layout", () => {
  test("is deterministic, pins the center at the origin and avoids overlaps", async () => {
    const input = layoutInputFor(MASTER_PAYLOAD);
    const a = await computeLayout(input, elk);
    const b = await computeLayout(input, elk);
    expect(a).toEqual(b);
    expect(a.transformer).toEqual({ x: 0, y: 0 });
    expect(Object.keys(a).sort()).toEqual(["adam", "attention", "dropout", "softmax", "transformer"]);
    noOverlap(a, "transformer");
  });

  test("keeps unlinked concepts near the center", async () => {
    const p = structuredClone(MASTER_PAYLOAD);
    p.layer.links = p.layer.links.slice(0, 2);
    const pos = await computeLayout(layoutInputFor(p), elk);
    for (const q of Object.values(pos)) expect(Math.hypot(q.x, q.y)).toBeLessThan(1200);
  });

  test("big layers use the deterministic ring layout", async () => {
    const big = syntheticLayer(300);
    const fail = () => Promise.reject(new Error("ELK must not run for 300 nodes"));
    const a = await computeLayout(layoutInputFor(big), fail);
    const b = await computeLayout(layoutInputFor(big), fail);
    expect(a).toEqual(b);
    expect(a.c0).toEqual({ x: 0, y: 0 });
    expect(Object.keys(a)).toHaveLength(300);
  });

  test("cache key changes with the concepts, not with their text", () => {
    const k1 = layoutKey("p", "master", layoutInputFor(MASTER_PAYLOAD));
    const renamed = structuredClone(MASTER_PAYLOAD);
    (renamed.concepts.attention as Concept).gist = "new gist";
    expect(layoutKey("p", "master", layoutInputFor(renamed))).toBe(k1);
    const fewer = payload("master", concept("transformer"), [concept("attention")]);
    expect(layoutKey("p", "master", layoutInputFor(fewer))).not.toBe(k1);
  });

  test("shape boundaries", () => {
    expect(boundaryDistance("concept_only", 100, 1, 0)).toBe(50);
    expect(boundaryDistance("in_code", 100, 1, 0)).toBeCloseTo(50);
    // Pointy-top hexagon: flat sides left/right (apothem), a vertex straight up.
    expect(boundaryDistance("in_library", 100, 1, 0)).toBeCloseTo(50 * Math.cos(Math.PI / 6));
    expect(boundaryDistance("in_library", 100, 0, -1)).toBeCloseTo(50);
  });
});

describe("edges and nodes (pure)", () => {
  test("edge geometry stops at the shape boundary and puts the label between the nodes", () => {
    const g = edgeGeometry({ x: 0, y: 0 }, { x: 400, y: 0 }, "concept_only", 100, "concept_only", 100);
    expect(g.path.startsWith("M")).toBe(true);
    expect(g.mid.x).toBeGreaterThan(150);
    expect(g.mid.x).toBeLessThan(250);
    const [tipX] = (g.arrow.split(" ")[0] ?? "").split(",").map(Number);
    expect(tipX).toBeLessThan(350);
    // Reverse direction bends to the other side.
    const r = edgeGeometry({ x: 400, y: 0 }, { x: 0, y: 0 }, "concept_only", 100, "concept_only", 100);
    expect(Math.sign(r.mid.y)).toBe(-Math.sign(g.mid.y));
  });

  test("stagger delays grow with distance and stay capped", () => {
    const d = enterDelays({ a: { x: 0, y: 0 }, b: { x: 10, y: 0 }, c: { x: 500, y: 0 } });
    expect(d.a).toBe(0);
    expect(d.b).toBeLessThanOrEqual(25);
    expect(d.c).toBeGreaterThan(d.b ?? 0);
    const many = enterDelays(Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`n${i}`, { x: i * 10, y: 0 }])));
    expect(Math.max(...Object.values(many))).toBeLessThanOrEqual(150);
  });

  test("nodes carry legend state", () => {
    const nodes = buildNodes(
      { layerId: "master", payload: MASTER_PAYLOAD, positions: { transformer: { x: 0, y: 0 } } },
      null,
      (id) => (id === "attention" ? 2 : null),
    );
    const att = nodes.find((n) => n.id === "attention");
    expect(att?.data.childCount).toBe(2);
    expect(att?.ariaLabel).toContain("can jump in");
    expect(nodes.find((n) => n.id === "transformer")?.data.center).toBe(true);
    expect(nodes.find((n) => n.id === "softmax")?.ariaLabel).toContain("first principle");
  });

  test("edge popover text is plain paragraphs", () => {
    expect(plainParagraphs("**Bold** and *it* `code`\n\nSecond   para")).toEqual(["Bold and it code", "Second para"]);
  });
});

describe("ChartCanvas", () => {
  test("renders legend shapes and states for each node", async () => {
    withClient(<ChartCanvas paperId="p" layerId="master" payload={MASTER_PAYLOAD} />);
    const node = (id: string) => document.querySelector(`.react-flow__node[data-id="${id}"] [data-grounding]`);
    await waitFor(() => expect(node("attention")).not.toBeNull(), { timeout: 5000 });
    expect(node("attention")?.getAttribute("data-grounding")).toBe("in_code");
    expect(node("softmax")?.getAttribute("data-grounding")).toBe("in_library");
    expect(node("dropout")?.getAttribute("data-grounding")).toBe("shown_by_running");
    expect(node("adam")?.getAttribute("data-grounding")).toBe("unknown");
    expect(node("transformer")?.getAttribute("data-state")).toContain("center");
    expect(node("dropout")?.getAttribute("data-state")).toContain("mismatch");
    expect(node("attention")?.textContent).toContain("↘");
    expect(node("softmax")?.textContent).not.toContain("↘");
    // Generating concept: title right away, shimmer instead of the gist.
    expect(node("adam")?.textContent).toContain("ADAM");
    expect(node("adam")?.querySelector("[aria-hidden='true']")).not.toBeNull();
    expect(screen.getByRole("group", { name: /ATTENTION, In the code, can jump in/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Legend")).toBeInTheDocument();
  });

  test("click selects, double-click jumps in, Back returns with the node selected", async () => {
    withClient(<ChartCanvas paperId="p" layerId="master" payload={MASTER_PAYLOAD} />);
    const el = await screen.findByRole("group", { name: /ATTENTION/ }, { timeout: 5000 });
    fireEvent.click(el);
    expect(useUI.getState().selectedId).toBe("attention");
    expect(useUI.getState().drawerOpen).toBe(true);
    // Selected: edges touching it turn accent.
    await waitFor(() => expect(document.querySelector("[data-active='true']")).not.toBeNull());
    fireEvent.doubleClick(el);
    expect(useUI.getState().path).toEqual(["attention"]);
    act(() => useUI.getState().back());
    expect(useUI.getState().path).toEqual([]);
    expect(useUI.getState().selectedId).toBe("attention");
    // A first principle can't be jumped into.
    fireEvent.doubleClick(screen.getByRole("group", { name: /SOFTMAX/ }));
    expect(useUI.getState().path).toEqual([]);
  });

  test("Enter jumps into the selected node; f fits; Tab cycles inside the canvas", async () => {
    withClient(<ChartCanvas paperId="p" layerId="master" payload={MASTER_PAYLOAD} />);
    const el = await screen.findByRole("group", { name: /ATTENTION/ }, { timeout: 5000 });
    el.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    const first = useUI.getState().selectedId;
    expect(first).not.toBeNull();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(useUI.getState().selectedId).not.toBe(first);
    act(() => useUI.getState().select("attention", false));
    fireEvent.keyDown(window, { key: "Enter" });
    expect(useUI.getState().path).toEqual(["attention"]);
  });

  test("double-clicking an edge label opens the popover anchored to it", async () => {
    withClient(<ChartCanvas paperId="p" layerId="master" payload={MASTER_PAYLOAD} />);
    await screen.findByRole("group", { name: /ATTENTION/ }, { timeout: 5000 });
    const labels = await screen.findAllByText("uses", {}, { timeout: 5000 });
    fireEvent.doubleClick(labels[0] as HTMLElement);
    const edge = useUI.getState().edge;
    expect(edge?.linkId).toMatch(/^master:transformer->/);
    expect(edge?.flowX).toBeTypeOf("number");
    const dialog = await screen.findByRole("dialog", { name: "Why these are linked" });
    expect(within(dialog).getByLabelText("Writing the explanation")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Open TRANSFORMER" })).toBeInTheDocument();
  });
});

describe("ChartView", () => {
  test("breadcrumbs show the path from cached layers; crumb click jumps out", async () => {
    useUI.setState({ path: ["attention"] });
    withClient(<ChartView />, (qc) => {
      qc.setQueryData(keys.layer("p", "master"), MASTER_PAYLOAD);
      qc.setQueryData(keys.layer("p", "attention"), CHILD_PAYLOAD);
    });
    const nav = await screen.findByRole("navigation", { name: "Where you are on the map" });
    expect(within(nav).getByRole("button", { name: "TRANSFORMER" })).toBeInTheDocument();
    expect(within(nav).getByText("ATTENTION")).toHaveAttribute("aria-current", "location");
    await screen.findByRole("group", { name: /QUERY/ }, { timeout: 5000 });
    fireEvent.click(within(nav).getByRole("button", { name: "TRANSFORMER" }));
    expect(useUI.getState().path).toEqual([]);
    expect(useUI.getState().selectedId).toBe("attention");
    await screen.findByRole("group", { name: /SOFTMAX/ }, { timeout: 5000 });
  });

  test("with motion: jump in fades the child layer in, and a new target cancels the old one", async () => {
    const mm = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: false, media: q })) as unknown as typeof window.matchMedia;
    try {
      withClient(<ChartView />, (qc) => {
        qc.setQueryData(keys.layer("p", "master"), MASTER_PAYLOAD);
        qc.setQueryData(keys.layer("p", "attention"), CHILD_PAYLOAD);
      });
      await screen.findByRole("group", { name: /SOFTMAX/ }, { timeout: 5000 });
      act(() => useUI.getState().jumpIn("attention"));
      // Mid-flight the old layer is still mounted (fading out) …
      expect(screen.getByRole("group", { name: /SOFTMAX/ })).toBeInTheDocument();
      // … then the child layer replaces it, with the staggered fade-in class on its nodes.
      const q = await screen.findByRole("group", { name: /QUERY/ }, { timeout: 2000 });
      expect(q.querySelector("[data-grounding]")?.className).toContain("enter");
      expect(screen.queryByRole("group", { name: /SOFTMAX/ })).toBeNull();
      // Back during nothing in flight: the parent returns with the node we came from selected.
      act(() => useUI.getState().back());
      await screen.findByRole("group", { name: /SOFTMAX/ }, { timeout: 2000 });
      expect(useUI.getState().selectedId).toBe("attention");
      // Jump in and immediately back: ends on the parent.
      act(() => useUI.getState().jumpIn("attention"));
      act(() => useUI.getState().back());
      await new Promise((r) => setTimeout(r, 700));
      expect(screen.getByRole("group", { name: /SOFTMAX/ })).toBeInTheDocument();
      expect(screen.queryByRole("group", { name: /QUERY/ })).toBeNull();
    } finally {
      window.matchMedia = mm;
    }
  });

  test("a layer still being generated shows a quiet status line", async () => {
    useUI.setState({ path: [] });
    globalThis.fetch = (() => Promise.resolve(new Response(JSON.stringify({ status: "generating" }), { status: 202 }))) as typeof fetch;
    withClient(<ChartView />);
    expect(await screen.findByText("Building the map…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back one level" })).toBeDisabled();
  });
});
