// Accessibility audit (spec §13.12) over the whole app: every view and dialog is rendered with
// representative data and checked for the things automated tools check first (names, labels,
// duplicate ids, tab order, landmarks). No axe here (no new dependencies), so the rules are ours.

import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { keys } from "../src/api/client";
import type { FAQItem, Finding, LayerPayload, PaperInfo, Route, Stop } from "../src/api/types";
import { App } from "../src/app/App";
import { useUI } from "../src/state/store";
import { CONCEPT, concept, CONCEPT_PAYLOAD, EXPLANATION, json, mockFetch, PAPER } from "./detailHarness";

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

// ---------------------------------------------------------------------------------------------
// The rules

/** Elements a screen reader or keyboard user can't reach: skipped by every rule. */
function unreachable(el: Element): boolean {
  return el.closest("[hidden], [inert], [aria-hidden='true']") !== null;
}

/** A small accessible-name computation: aria-labelledby, aria-label, label, content, title, alt. */
export function nameOf(el: Element): string {
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    const text = by
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ")
      .trim();
    if (text) return text;
  }
  const label = el.getAttribute("aria-label")?.trim();
  if (label) return label;
  if (el.id) {
    const forLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (forLabel?.textContent?.trim()) return forLabel.textContent.trim();
  }
  const wrapping = el.closest("label");
  if (wrapping?.textContent?.trim()) return wrapping.textContent.trim();
  for (const img of el.querySelectorAll("svg[aria-label], img[alt]")) {
    const n = (img.getAttribute("aria-label") ?? img.getAttribute("alt") ?? "").trim();
    if (n) return n;
  }
  const text = el.textContent?.trim();
  if (text) return text;
  return el.getAttribute("title")?.trim() ?? "";
}

const CONTROLS =
  "button, a[href], input:not([type='hidden']), select, textarea, [role='button'], [role='tab'], [role='option'], [role='combobox'], [role='switch'], [tabindex]:not([tabindex='-1'])";

function problems(root: ParentNode = document): string[] {
  const out: string[] = [];
  const describe = (el: Element) => `<${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""} class="${el.getAttribute("class") ?? ""}">`;

  for (const el of root.querySelectorAll(CONTROLS)) {
    if (unreachable(el)) continue;
    if (!nameOf(el)) out.push(`no accessible name: ${describe(el)}`);
  }
  const seen = new Set<string>();
  for (const el of root.querySelectorAll("[id]")) {
    if (seen.has(el.id)) out.push(`duplicate id "${el.id}"`);
    seen.add(el.id);
  }
  for (const d of root.querySelectorAll("[role='dialog']")) {
    if (unreachable(d)) continue;
    if (!nameOf(d)) out.push(`dialog without a name: ${describe(d)}`);
  }
  for (const el of root.querySelectorAll("[tabindex]")) {
    if (Number(el.getAttribute("tabindex")) > 0) out.push(`positive tabindex: ${describe(el)}`);
  }
  for (const img of root.querySelectorAll("[role='img']")) {
    if (!unreachable(img) && !nameOf(img)) out.push(`image without a name: ${describe(img)}`);
  }
  // A hidden drawer must not be in the tab order.
  for (const hidden of root.querySelectorAll("aside[aria-hidden='true']")) {
    for (const el of hidden.querySelectorAll(CONTROLS)) {
      if (el.closest("[inert]") === null) out.push(`focusable inside a hidden panel: ${describe(el)}`);
    }
  }
  // Every label points at something.
  for (const l of root.querySelectorAll("label[for]")) {
    if (!document.getElementById(l.getAttribute("for") ?? "")) out.push(`label for a missing control: ${l.textContent}`);
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// Representative data

const MASTER: LayerPayload = {
  layer: {
    id: "master",
    paper_id: PAPER,
    center: "transformer",
    concepts: [CONCEPT, "softmax", "position"],
    links: [
      { id: `master:transformer->${CONCEPT}`, layer_id: "master", source: "transformer", target: CONCEPT, label: "is built from", kind: "part_of" },
      { id: `master:${CONCEPT}->softmax`, layer_id: "master", source: CONCEPT, target: "softmax", label: "normalizes with", kind: "uses" },
    ],
    gen_state: "detailed",
  },
  concepts: {
    transformer: concept("transformer", "Transformer", { depth: 0, has_layer: true, grounding: "concept_only" }),
    [CONCEPT]: concept(CONCEPT, "Self-attention", { has_layer: true, anchors: ["eq:1"] }),
    softmax: concept("softmax", "Softmax", { grounding: "in_library", issue_count_self: 1 }),
    position: concept("position", "Positional encoding", { grounding: "shown_by_running", is_first_principle: true }),
  },
};

const PAPER_INFO: PaperInfo = {
  paper_id: PAPER,
  status: "ready",
  title: "Attention Is All You Need",
  authors: ["A. Vaswani", "N. Shazeer"],
  arxiv_id: "1706.03762",
  version: "v7",
  repo_status: { kind: "official_found", message: "Official code found" },
  chart_ready: true,
};

const stop = (n: number, over: Partial<Stop> = {}): Stop => ({
  id: `s${n}`,
  route_id: "main",
  order: n,
  title: `Stop ${n} title`,
  section_label: `§${n}`,
  anchor_id: `sec:${n}`,
  role: "other",
  concept_id: n === 2 ? CONCEPT : null,
  summary: `The paper says thing ${n}.`,
  transition_to_next: n < 3 ? "so the authors continue," : null,
  sub_route_id: n === 2 ? "s2" : null,
  segment_title: n === 1 ? "§1 Introduction" : null,
  first_stop_id: null,
  seen_again_at: [],
  side_roads: [],
  ...over,
});

const ROUTE: Route = { id: "main", paper_id: PAPER, parent_stop_id: null, stops: [stop(1), stop(2), stop(3)] };

const finding: Finding = {
  id: "f1",
  claim_text: "The model uses eight attention heads.",
  claim_type: "hyperparameter",
  paper_anchor: "sec:3.2.2",
  code_spans: [{ repo_id: "r", commit_sha: "a".repeat(40), path: "model.py", start_line: 10, end_line: 12 }],
  verdict: "mismatch",
  severity: "high",
  confidence: 0.9,
  method: "literal_match",
  evidence: "Paper: 8 heads. Code: HEADS = 4.",
  concept_ids: ["softmax"],
};

const FAQ: FAQItem[] = [
  {
    id: "q1",
    question: "Why divide by the square root of d_k?",
    answer_markdown: "It keeps the scores in a range where softmax still has a useful gradient.",
    source_kind: "reviewer_discussion",
    source_url: "https://openreview.net/forum?id=abc",
    source_label: "From reviewer discussion",
    concept_ids: [CONCEPT],
    anchors_cited: ["eq:1"],
    supported: true,
  },
];

function seeded(): QueryClient {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(keys.paper(PAPER), PAPER_INFO);
  qc.setQueryData(keys.layer(PAPER, "master"), MASTER);
  qc.setQueryData(keys.route(PAPER, "main"), ROUTE);
  qc.setQueryData(keys.findings(PAPER), { findings: [finding], state: "done" });
  qc.setQueryData(keys.faq(PAPER), { items: FAQ });
  qc.setQueryData(keys.concept(PAPER, CONCEPT), CONCEPT_PAYLOAD());
  qc.setQueryData(keys.explanation(PAPER, CONCEPT, "default"), EXPLANATION);
  return qc;
}

function backend() {
  mockFetch((url) => {
    if (url.endsWith("/api/health")) return json({ status: "ok" });
    if (url.endsWith("/api/budget"))
      return json({ day: "d", requests_today: 3, limits: { rpm: 8, rpd: 1400, tpm: 2e5 }, provider: "gemini", models: [] });
    if (url.endsWith("/api/settings"))
      return json({
        version: "0.0.1",
        provider: "gemini",
        models: { fast: "f", default: "d", strong: "s" },
        limits: { rpm: 8, rpd: 1400, tpm: 2e5 },
        free_tier_notice: true,
        sandbox_mode: "process",
      });
    return undefined;
  });
}

function mount(state: Partial<ReturnType<typeof useUI.getState>> = {}) {
  window.history.replaceState(null, "", "/"); // a second mount in one test must not reopen ?paper=
  backend();
  act(() => {
    useUI.setState({
      paperId: PAPER,
      view: "chart",
      path: [],
      selectedId: null,
      drawerOpen: false,
      edge: null,
      dialog: null,
      findingsOpen: false,
      pendingFocus: null,
      routePath: [],
      stopId: null,
      ...state,
    });
  });
  return render(<App client={seeded()} />);
}

beforeEach(() => window.history.replaceState(null, "", "/")); // the app keeps ?paper= in the URL
afterEach(() => vi.unstubAllGlobals());

// ---------------------------------------------------------------------------------------------

describe("the empty first screen", () => {
  test("has a name for every control, one main landmark and the skip link first", () => {
    backend();
    act(() => useUI.setState({ paperId: null, dialog: null }));
    render(<App client={new QueryClient()} />);
    expect(problems()).toEqual([]);
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    const first = document.querySelector<HTMLElement>("a, button, input");
    expect(first).toHaveTextContent("Skip to content");
    expect(first?.getAttribute("href")).toBe("#main");
    expect(document.getElementById("main")).not.toBeNull();
  });

  test("the skip link moves focus to the main region", () => {
    backend();
    act(() => useUI.setState({ paperId: null }));
    render(<App client={new QueryClient()} />);
    fireEvent.click(screen.getByText("Skip to content"));
    expect(document.getElementById("main")).toHaveFocus();
  });

  test("the example papers are real buttons", () => {
    backend();
    act(() => useUI.setState({ paperId: null }));
    render(<App client={new QueryClient()} />);
    expect(screen.getByRole("button", { name: "Attention Is All You Need" })).toBeInTheDocument();
  });

  test("a server that isn't running is announced, with what to do", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    act(() => useUI.setState({ paperId: null }));
    render(<App client={new QueryClient()} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't reach the Portolan server. Start it with make dev.");
  });
});

describe("every view", () => {
  test("Chart with the drawer open", async () => {
    mount({ selectedId: CONCEPT, drawerOpen: true });
    await screen.findAllByRole("group", { name: /Self-attention/ }, { timeout: 8000 });
    await waitFor(() => expect(screen.getByRole("complementary", { name: "Concept details" })).toBeInTheDocument());
    expect(problems()).toEqual([]);
  });

  test("Chart with the drawer closed keeps nothing of it in the tab order", async () => {
    mount();
    await screen.findAllByRole("group", { name: /Self-attention/ }, { timeout: 8000 });
    expect(problems()).toEqual([]);
  });

  test("Route", async () => {
    mount({ view: "route" });
    await screen.findAllByRole("group", { name: /^Stop 1,/ }, { timeout: 8000 });
    expect(problems()).toEqual([]);
  });

  test("FAQ", async () => {
    mount({ view: "faq" });
    await screen.findByText("Why divide by the square root of d_k?");
    expect(problems()).toEqual([]);
  });

  test("Findings panel", async () => {
    mount({ findingsOpen: true });
    await screen.findByText("The model uses eight attention heads.");
    expect(problems()).toEqual([]);
  });
});

describe("dialogs", () => {
  test.each(["settings", "help"] as const)("%s is labelled, modal and complete", async (dialog) => {
    mount({ dialog });
    const d = await screen.findByRole("dialog");
    expect(d).toHaveAttribute("aria-modal", "true");
    expect(problems()).toEqual([]);
    expect(d.contains(document.activeElement)).toBe(true);
  });

  test("search is a labelled combobox that owns its listbox", async () => {
    mount({ dialog: "search" });
    const box = await screen.findByRole("combobox", { name: "Search concepts" });
    expect(box).toHaveAttribute("aria-controls");
    expect(document.getElementById(box.getAttribute("aria-controls") ?? "")).toHaveAttribute("role", "listbox");
    expect(problems()).toEqual([]);
  });
});

describe("selection is visible without color", () => {
  test("the selected Chart node and the current Route stop say so to a screen reader", async () => {
    const chart = mount({ selectedId: CONCEPT, drawerOpen: false });
    const nodes = await screen.findAllByRole("group", { name: /^Self-attention,/ }, { timeout: 8000 });
    expect(nodes[0]).toHaveAttribute("aria-current", "true");
    expect(document.querySelectorAll(".react-flow__node[aria-current='true']")).toHaveLength(1);
    act(() => useUI.getState().select(null));
    await waitFor(() => expect(document.querySelectorAll(".react-flow__node[aria-current]")).toHaveLength(0));
    chart.unmount();

    mount({ view: "route", stopId: "s2" });
    const stops = await screen.findAllByRole("group", { name: /^Stop 2,/ }, { timeout: 8000 });
    expect(stops[0]).toHaveAttribute("aria-current", "true");
  });
});

describe("the rules themselves", () => {
  test("nameOf follows aria-labelledby, aria-label, label[for] and content", () => {
    const { container } = render(
      <div>
        <span id="t">Title</span>
        <button aria-labelledby="t" />
        <button aria-label="Close" />
        <label htmlFor="i">Paper</label>
        <input id="i" />
        <button>Open</button>
        <button />
      </div>,
    );
    const names = [...container.querySelectorAll("button, input")].map(nameOf);
    expect(names).toEqual(["Title", "Close", "Paper", "Open", ""]);
  });

  test("problems() flags what it should", () => {
    const { container } = render(
      <div>
        <button />
        <div id="x" />
        <div id="x" />
        <div role="dialog" />
        <button tabIndex={3}>Positive</button>
      </div>,
    );
    const found = problems(container).join("\n");
    expect(found).toMatch(/no accessible name/);
    expect(found).toMatch(/duplicate id "x"/);
    expect(found).toMatch(/dialog without a name/);
    expect(found).toMatch(/positive tabindex/);
  });
});
