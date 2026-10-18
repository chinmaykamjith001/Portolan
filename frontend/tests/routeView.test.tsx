import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";

import { keys } from "../src/api/client";
import type { Concept, ConceptPayload, LayerPayload, Route, Stop } from "../src/api/types";
import ChartView from "../src/chart/ChartView";
import { resolveChartPath } from "../src/chart/focus";
import { DetailDrawer } from "../src/detail/DetailDrawer";
import { RouteCanvas } from "../src/route/RouteView";
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
  // Reduced motion: route swaps are instant, so tests don't wait on animation timers.
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

const PAPER = "p";

function reset(over: Partial<ReturnType<typeof useUI.getState>> = {}) {
  useUI.setState({
    paperId: PAPER,
    view: "route",
    path: [],
    selectedId: null,
    drawerOpen: false,
    edge: null,
    cameFrom: null,
    routePath: [],
    stopId: null,
    cameFromStop: null,
    pendingFocus: null,
    ...over,
  });
}

beforeEach(() => reset());
afterEach(() => vi.unstubAllGlobals());

function stop(n: number, over: Partial<Stop> = {}): Stop {
  return {
    id: `s${n}`,
    route_id: "main",
    order: n,
    title: `Stop title ${n}`,
    section_label: `§${n}`,
    anchor_id: `sec:${n}`,
    role: "other",
    concept_id: `c${n}`,
    summary: `The paper says thing ${n}.`,
    transition_to_next: null,
    sub_route_id: null,
    segment_title: null,
    first_stop_id: null,
    seen_again_at: [],
    side_roads: [],
    ...over,
  };
}

const MAIN_ROUTE: Route = {
  id: "main",
  paper_id: PAPER,
  parent_stop_id: null,
  stops: [
    stop(1, { segment_title: "§1 Introduction", transition_to_next: "so the authors drop recurrence," }),
    stop(2, { segment_title: "§2 Method", sub_route_id: "s2", side_roads: ["sec:B"] }),
    stop(3, { seen_again_at: ["s6"] }),
    stop(4, { concept_id: null }),
    stop(5, { sub_route_id: "s5" }),
    stop(6, { first_stop_id: "s3" }),
    stop(7),
  ],
};

const SUB_ROUTE: Route = {
  id: "s2",
  paper_id: PAPER,
  parent_stop_id: "s2",
  stops: [stop(1, { id: "t1", route_id: "s2", title: "Inside one", concept_id: "c2" }), stop(2, { id: "t2", route_id: "s2", title: "Inside two", concept_id: null })],
};

function concept(id: string, over: Partial<Concept> = {}): Concept {
  return {
    id,
    canonical_name: id,
    title: `Concept ${id}`,
    gist: "A gist.",
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

function wrap(node: ReactNode, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <div style={{ width: 800, height: 600, position: "relative" }}>{node}</div>
    </QueryClientProvider>,
  );
}

const stopNodes = () => screen.getAllByRole("group").filter((el) => /^Stop \d+,/.test(el.getAttribute("aria-label") ?? ""));
const stopNode = (n: number) => screen.getByLabelText(new RegExp(`^Stop ${n},`));

function Canvas({ concepts }: { concepts?: Record<string, Concept> }) {
  const routeId = useUI((s) => s.routePath[s.routePath.length - 1] ?? "main");
  const route = routeId === "main" ? MAIN_ROUTE : routeId === "s2" ? SUB_ROUTE : null;
  return <RouteCanvas paperId={PAPER} routeId={routeId} route={route} concepts={concepts} />;
}

describe("the Route canvas", () => {
  test("stops come in the paper's order, with section labels, markers and transitions", () => {
    wrap(<Canvas />);
    const labels = stopNodes().map((el) => el.getAttribute("aria-label") ?? "");
    expect(labels).toHaveLength(7);
    labels.forEach((l, i) => expect(l.startsWith(`Stop ${i + 1}, §${i + 1}, Stop title ${i + 1}`)).toBe(true));
    expect(screen.getByText("§1 Introduction")).toBeInTheDocument();
    expect(screen.getByText("§2 Method")).toBeInTheDocument();
    expect(screen.getByText("so the authors drop recurrence,")).toBeInTheDocument();
  });

  test("stops lay out across three columns, one row each", () => {
    wrap(<Canvas />);
    const pos = (n: number) => {
      const t = stopNode(n).style.transform; // translate(Xpx, Ypx)
      const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(t);
      return { x: Number(m?.[1]), y: Number(m?.[2]) };
    };
    const xs = [1, 2, 3, 4, 5, 6, 7].map((n) => pos(n).x);
    expect(xs[1]).toBeGreaterThan(xs[0] ?? 0); // center → right
    expect(xs[2]).toBeLessThan(xs[0] ?? 0); //   right → left
    expect(xs[3]).toBe(xs[0]); //                left → center again
    const ys = [1, 2, 3].map((n) => pos(n).y);
    expect((ys[1] ?? 0) - (ys[0] ?? 0)).toBe((ys[2] ?? 0) - (ys[1] ?? 0)); // even vertical spacing
  });

  test("a stop shows its grounding, a mismatch mark, recurrence and the jump affordance", () => {
    wrap(
      <Canvas
        concepts={{
          c1: concept("c1", { grounding: "in_code" }),
          c3: concept("c3", { grounding: "in_library", issue_count_self: 1 }),
        }}
      />,
    );
    expect(within(stopNode(1)).getByRole("img", { name: "In the code" })).toBeInTheDocument();
    expect(within(stopNode(3)).getByRole("img", { name: "In a library" })).toBeInTheDocument();
    expect(within(stopNode(3)).getByText("!")).toBeInTheDocument();
    expect(stopNode(3).getAttribute("aria-label")).toContain("mismatch with the code");
    expect(within(stopNode(1)).queryByText("!")).toBeNull();
    expect(within(stopNode(3)).getByText("Seen again at stop 6")).toBeInTheDocument();
    expect(within(stopNode(6)).getByText("First mentioned at stop 3")).toBeInTheDocument();
    expect(within(stopNode(2)).getByRole("button", { name: "Jump in" })).toBeInTheDocument();
    expect(within(stopNode(1)).queryByRole("button", { name: /Jump in/ })).toBeNull();
    expect(within(stopNode(2)).getByLabelText("Side road: Appendix B")).toBeInTheDocument();
  });

  test("clicking a stop makes it current and opens the drawer on its concept", () => {
    wrap(<Canvas />);
    fireEvent.click(stopNode(3));
    const s = useUI.getState();
    expect(s.stopId).toBe("s3");
    expect(s.selectedId).toBe("c3");
    expect(s.drawerOpen).toBe(true);
    expect(within(stopNode(3)).getByText("Stop title 3").closest("[data-state]")?.getAttribute("data-state")).toBe("current");
  });

  test("a stop without a concept still opens the drawer", () => {
    wrap(<Canvas />);
    fireEvent.click(stopNode(4));
    const s = useUI.getState();
    expect(s.stopId).toBe("s4");
    expect(s.selectedId).toBeNull();
    expect(s.drawerOpen).toBe(true);
  });

  test("← and → move along the route and stop at its ends", () => {
    wrap(<Canvas />);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(useUI.getState().stopId).toBe("s1");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(useUI.getState().stopId).toBe("s3");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(useUI.getState().stopId).toBe("s2");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(useUI.getState().stopId).toBe("s1");
    for (let i = 0; i < 10; i++) fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(useUI.getState().stopId).toBe("s7");
  });

  test("arrow keys are ignored while typing", () => {
    wrap(
      <>
        <input aria-label="Ask" />
        <Canvas />
      </>,
    );
    const input = screen.getByLabelText("Ask");
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowRight" });
    expect(useUI.getState().stopId).toBeNull();
  });

  test("Next and Previous buttons move the current stop, and say where you are", () => {
    wrap(<Canvas />);
    const prev = screen.getByRole("button", { name: "Previous stop" });
    const next = screen.getByRole("button", { name: "Next stop" });
    fireEvent.click(next);
    fireEvent.click(next);
    expect(useUI.getState().stopId).toBe("s2");
    expect(screen.getByText("Stop 2 of 7")).toBeInTheDocument();
    fireEvent.click(prev);
    expect(useUI.getState().stopId).toBe("s1");
    expect(prev).toBeDisabled();
  });

  test("traveled connectors are drawn in the accent, the rest are not", () => {
    wrap(<Canvas />);
    fireEvent.click(stopNode(3));
    const traveled = document.querySelectorAll("[data-traveled]");
    expect(traveled).toHaveLength(2); // 1→2 and 2→3
  });

  test("Enter jumps into the current stop; one without a sub-route stays put", () => {
    wrap(<Canvas />);
    fireEvent.click(stopNode(1));
    fireEvent.keyDown(window, { key: "Enter" });
    expect(useUI.getState().routePath).toEqual([]);
    fireEvent.click(stopNode(2));
    fireEvent.keyDown(window, { key: "Enter" });
    expect(useUI.getState().routePath).toEqual(["s2"]);
  });

  test("double-click and the Jump in button open the sub-route and stay on the Route", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(keys.route(PAPER, "main"), MAIN_ROUTE); // breadcrumb titles come from the cache
    wrap(<Canvas />, qc);
    fireEvent.doubleClick(stopNode(1));
    expect(useUI.getState().routePath).toEqual([]);
    fireEvent.doubleClick(stopNode(2));
    expect(useUI.getState().routePath).toEqual(["s2"]);
    expect(useUI.getState().view).toBe("route");
    await waitFor(() => expect(screen.getByLabelText(/^Stop 1, §1, Inside one/)).toBeInTheDocument());
    expect(screen.queryByLabelText(/^Stop 1, §1, Stop title 1/)).toBeNull();
    expect(screen.getByRole("navigation", { name: "Where you are on the route" })).toHaveTextContent(/Route\s*›\s*Stop title 2/);
  });

  test("Back returns to the parent route with the stop we came from current", async () => {
    wrap(<Canvas />);
    fireEvent.click(stopNode(2));
    act(() => useUI.getState().routeJumpIn("s2"));
    await waitFor(() => expect(screen.getByLabelText(/^Stop 1, §1, Inside one/)).toBeInTheDocument());
    act(() => useUI.getState().escape()); // no drawer open → Esc goes back
    await waitFor(() => expect(screen.getByLabelText(/^Stop 2, §2, Stop title 2/)).toBeInTheDocument());
    await waitFor(() => expect(useUI.getState().stopId).toBe("s2"));
    expect(useUI.getState().routePath).toEqual([]);
    expect(useUI.getState().cameFromStop).toBeNull();
    expect(useUI.getState().view).toBe("route");
  });

  test("a breadcrumb takes you straight back to the main route", async () => {
    wrap(<Canvas />);
    act(() => useUI.getState().routeJumpIn("s2"));
    await waitFor(() => expect(screen.getByLabelText(/^Stop 1, §1, Inside one/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Route" }));
    await waitFor(() => expect(screen.getByLabelText(/^Stop 7,/)).toBeInTheDocument());
    expect(useUI.getState().routePath).toEqual([]);
  });

  test("while the road is being laid out there is a sentence, not an empty box", () => {
    wrap(<RouteCanvas paperId={PAPER} routeId="main" route={null} status="Laying out the road…" emptyText="Laying out the road…" />);
    expect(screen.getAllByText("Laying out the road…").length).toBeGreaterThan(0);
    expect(screen.queryAllByRole("group").filter((el) => /^Stop /.test(el.getAttribute("aria-label") ?? ""))).toHaveLength(0);
  });

  test("a route with no stops says so", () => {
    wrap(
      <RouteCanvas
        paperId={PAPER}
        routeId="main"
        route={{ ...MAIN_ROUTE, stops: [] }}
        status="There are no stops on this road yet."
        emptyText="There are no stops on this road yet."
      />,
    );
    expect(screen.getAllByText("There are no stops on this road yet.").length).toBeGreaterThan(0);
  });
});

describe("the store on the Route", () => {
  test("Esc closes the drawer first, then goes up one route", () => {
    reset({ routePath: ["s2", "t1"], stopId: "t2", selectedId: "c2", drawerOpen: true });
    useUI.getState().escape();
    expect(useUI.getState().drawerOpen).toBe(false);
    expect(useUI.getState().routePath).toEqual(["s2", "t1"]);
    useUI.getState().escape();
    expect(useUI.getState().routePath).toEqual(["s2"]);
    expect(useUI.getState().stopId).toBe("t1");
    expect(useUI.getState().cameFromStop).toBe("t1");
    useUI.getState().escape();
    useUI.getState().escape(); // nothing left to go back out of
    expect(useUI.getState().routePath).toEqual([]);
    expect(useUI.getState().view).toBe("route");
  });

  test("jumping to a breadcrumb keeps the routes above it", () => {
    reset({ routePath: ["s2", "t1", "u1"] });
    useUI.getState().routeJumpTo(1);
    expect(useUI.getState().routePath).toEqual(["s2"]);
    expect(useUI.getState().stopId).toBe("t1");
  });
});

describe("the drawer on the Route", () => {
  function seeded() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(keys.route(PAPER, "main"), MAIN_ROUTE);
    // Everything else (concept, explanation) is "still generating".
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 202 })));
    return qc;
  }

  test("shows what the paper says here above the concept, with a way to the Chart", () => {
    const qc = seeded();
    reset({ routePath: [], stopId: "s3", selectedId: "c3", drawerOpen: true });
    wrap(<DetailDrawer />, qc);
    const drawer = screen.getByRole("complementary", { name: "Concept details" });
    expect(within(drawer).getByText("Route · stop 3 · §3")).toBeInTheDocument();
    expect(within(drawer).getByRole("region", { name: "What the paper says here" })).toHaveTextContent("The paper says thing 3.");
    expect(within(drawer).getByText("Seen again at", { exact: false })).toBeInTheDocument();
    expect(within(drawer).getByRole("tab", { name: "Explain" })).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole("button", { name: "Show on Chart" }));
    expect(useUI.getState().view).toBe("chart");
    expect(useUI.getState().pendingFocus).toEqual({ conceptId: "c3", view: "chart" });
  });

  test("a recurring concept's links move the current stop", () => {
    const qc = seeded();
    reset({ routePath: [], stopId: "s6", selectedId: "c6", drawerOpen: true });
    wrap(<DetailDrawer />, qc);
    const drawer = screen.getByRole("complementary", { name: "Concept details" });
    expect(within(drawer).getByText("First mentioned at", { exact: false })).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole("button", { name: /stop 3/ }));
    expect(useUI.getState().stopId).toBe("s3");
    expect(useUI.getState().selectedId).toBe("c3");
  });

  test("a stop without a concept shows only the summary", () => {
    const qc = seeded();
    reset({ routePath: [], stopId: "s4", selectedId: null, drawerOpen: true });
    wrap(<DetailDrawer />, qc);
    const drawer = screen.getByRole("complementary", { name: "Concept details" });
    expect(within(drawer).getByRole("heading", { level: 2, name: "Stop title 4" })).toBeInTheDocument();
    expect(within(drawer).getByRole("region", { name: "What the paper says here" })).toHaveTextContent("The paper says thing 4.");
    expect(within(drawer).queryByRole("tab")).toBeNull();
  });

  test("on the Chart, a concept can be found on the Route", () => {
    const qc = seeded();
    const layer: LayerPayload = {
      layer: { id: "master", paper_id: PAPER, center: "root", concepts: ["c3"], links: [], gen_state: "detailed" },
      concepts: { root: concept("root", { depth: 0, title: "Root" }), c3: concept("c3", { title: "Concept c3" }) },
    };
    qc.setQueryData(keys.layer(PAPER, "master"), layer);
    reset({ view: "chart", selectedId: "c3", drawerOpen: true });
    wrap(<DetailDrawer />, qc);
    const drawer = screen.getByRole("complementary", { name: "Concept details" });
    expect(within(drawer).queryByRole("region", { name: "What the paper says here" })).toBeNull();
    fireEvent.click(within(drawer).getByRole("button", { name: "Find on Route" }));
    expect(useUI.getState().view).toBe("route");
    expect(useUI.getState().pendingFocus).toEqual({ conceptId: "c3", view: "route" });
  });
});

describe("Show on Chart", () => {
  function conceptPayload(c: Concept, alsoAppearsIn: string[]): ConceptPayload {
    return {
      concept: c,
      also_appears_in: alsoAppearsIn,
      issues_inside: 0,
      details: { explanation: false, code: false, example: false, graph: false },
    };
  }

  function stubConcepts(map: Record<string, ConceptPayload>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const m = /\/concepts\/([^/?]+)$/.exec(String(url));
        const body = m ? map[decodeURIComponent(m[1] ?? "")] : undefined;
        if (!body) return new Response(JSON.stringify({ error: { code: "not_found", message: "no" } }), { status: 404 });
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
  }

  const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

  test("a concept on the master map needs no jumps", async () => {
    stubConcepts({ a: conceptPayload(concept("a"), ["master"]) });
    expect(await resolveChartPath(client(), PAPER, "a")).toEqual([]);
  });

  test("a deep concept is reached through its parents, outermost first", async () => {
    stubConcepts({
      leaf: conceptPayload(concept("leaf", { depth: 3 }), ["mid"]),
      mid: conceptPayload(concept("mid", { depth: 2 }), ["top"]),
      top: conceptPayload(concept("top", { depth: 1 }), ["master"]),
    });
    expect(await resolveChartPath(client(), PAPER, "leaf")).toEqual(["top", "mid"]);
  });

  test("when a concept lives in several layers, the shallowest parent wins", async () => {
    stubConcepts({
      leaf: conceptPayload(concept("leaf", { depth: 4 }), ["deep", "shallow"]),
      deep: conceptPayload(concept("deep", { depth: 3 }), ["x"]),
      shallow: conceptPayload(concept("shallow", { depth: 1 }), ["master"]),
      x: conceptPayload(concept("x", { depth: 2 }), ["master"]),
    });
    expect(await resolveChartPath(client(), PAPER, "leaf")).toEqual(["shallow"]);
  });

  test("an unknown concept, or a cycle, gives up instead of looping", async () => {
    stubConcepts({
      a: conceptPayload(concept("a", { depth: 2 }), ["b"]),
      b: conceptPayload(concept("b", { depth: 2 }), ["a"]),
    });
    expect(await resolveChartPath(client(), PAPER, "nope")).toBeNull();
    expect(await resolveChartPath(client(), PAPER, "a")).toBeNull();
  });

  test("the Chart consumes the request: it sets the path, selects the concept and clears the focus", async () => {
    const root = concept("root", { depth: 0, title: "Root", has_layer: true });
    const parent = concept("parent", { title: "Parent", has_layer: true });
    const leaf = concept("leaf", { depth: 2, title: "Leaf" });
    const layer = (id: string, center: Concept, others: Concept[]): LayerPayload => ({
      layer: { id, paper_id: PAPER, center: center.id, concepts: others.map((c) => c.id), links: [], gen_state: "detailed" },
      concepts: Object.fromEntries([center, ...others].map((c) => [c.id, c])),
    });
    const concepts: Record<string, ConceptPayload> = {
      leaf: conceptPayload(leaf, ["parent"]),
      parent: conceptPayload(parent, ["master"]),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url);
        const c = /\/concepts\/([^/?]+)$/.exec(u);
        let body: unknown;
        if (c) body = concepts[decodeURIComponent(c[1] ?? "")];
        else if (u.endsWith("/chart/master")) body = layer("master", root, [parent]);
        else if (u.endsWith("/chart/layers/parent")) body = layer("parent", parent, [leaf]);
        return body ? new Response(JSON.stringify(body), { status: 200 }) : new Response("{}", { status: 404 });
      }),
    );
    reset({ view: "chart", path: [], pendingFocus: { conceptId: "leaf", view: "chart" } });
    wrap(<ChartView />, client());
    await waitFor(() => expect(useUI.getState().pendingFocus).toBeNull());
    expect(useUI.getState().path).toEqual(["parent"]);
    expect(useUI.getState().selectedId).toBe("leaf");
    expect(useUI.getState().drawerOpen).toBe(true);
  });
});
