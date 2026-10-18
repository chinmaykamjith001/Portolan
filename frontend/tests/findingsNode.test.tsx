import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";

import type { Concept, LayerPayload } from "../src/api/types";
import { ChartCanvas } from "../src/chart/ChartView";
import { useUI } from "../src/state/store";

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

function concept(id: string, over: Partial<Concept> = {}): Concept {
  return {
    id,
    canonical_name: id,
    title: id.toUpperCase(),
    gist: `${id} gist`,
    grounding: "in_code",
    is_first_principle: false,
    depth: 1,
    anchors: [],
    has_layer: true,
    gen_state: "detailed",
    issue_count_self: 0,
    ...over,
  };
}

const PAYLOAD: LayerPayload = {
  layer: { id: "master", paper_id: "p", center: "root", concepts: ["bad", "parent", "fine"], links: [], gen_state: "detailed" },
  concepts: {
    root: concept("root", { depth: 0 }),
    bad: concept("bad", { issue_count_self: 1 }),
    parent: concept("parent", { issues_inside: 2 }),
    fine: concept("fine"),
  },
};

test("a mismatching concept shows the outline state and a ! glyph; its parent shows the count", async () => {
  useUI.setState({ paperId: "p", view: "chart", path: [], selectedId: null, drawerOpen: false });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ChartCanvas paperId="p" layerId="master" payload={PAYLOAD} />
    </QueryClientProvider>,
  );
  const node = (id: string) => document.querySelector(`.react-flow__node[data-id="${id}"] [data-grounding]`);
  await waitFor(() => expect(node("bad")).not.toBeNull(), { timeout: 5000 });
  expect(node("bad")?.getAttribute("data-state")).toContain("mismatch");
  expect(node("bad")?.textContent).toContain("!");
  expect(node("parent")?.getAttribute("data-state")).not.toContain("mismatch");
  expect(node("parent")?.querySelector("[aria-label='2 issues inside']")?.textContent).toBe("2");
  expect(node("fine")?.textContent).not.toContain("!");
  expect(node("fine")?.querySelector("[aria-label$='issues inside']")).toBeNull();
});
