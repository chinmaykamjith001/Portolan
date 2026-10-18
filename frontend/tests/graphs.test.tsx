import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { GraphSpec } from "../src/api/types";
import { GraphView } from "../src/detail/graphs";
import { histogram, parseGraph } from "../src/detail/graphs/data";
import { fit, fmt, niceRange } from "../src/detail/graphs/scale";
import {
  CONCEPT,
  CONCEPT_PAYLOAD,
  EXPLANATION,
  json,
  mockFetch,
  paperUrl,
  PAPER,
  renderDrawer,
} from "./detailHarness";

const spec = (template: GraphSpec["template"], data: Record<string, unknown>): GraphSpec => ({
  concept_id: CONCEPT,
  template,
  caption: "Softmax weights by score",
  data,
  note: "Higher scores take more of the weight.",
});

const GOOD: Record<GraphSpec["template"], Record<string, unknown>> = {
  bar: { x: ["a", "b", "c"], series: [{ name: "weight", y: [0.66, 0.24, 0.1] }] },
  line: {
    x: [1, 2, 3, 4],
    series: [
      { name: "train", y: [4, 3, 2, 1.5] },
      { name: "val", y: [4.2, 3.4, 2.6, 2.4] },
    ],
  },
  heatmap: { rows: ["q1", "q2"], cols: ["k1", "k2", "k3"], values: [[0.1, 0.8, 0.1], [0.5, 0.3, 0.2]] },
  before_after: { labels: ["small", "large"], before: [0.1, 0.9], after: [0.3, 0.7] },
  distribution: { x: [1, 2, 3, 4], y: [3, 5, 2, 1], style: "bars", bin_width: 1, x_label: "score", y_label: "count" },
};

const BAD: Record<GraphSpec["template"], Record<string, unknown>> = {
  bar: { x: ["a", "b"], series: [{ name: "w", y: [1] }] }, // y shorter than x
  line: { x: [], series: [] },
  heatmap: { rows: ["q"], cols: ["k1", "k2"], values: [[1]] }, // ragged
  before_after: { labels: ["a"], before: [1], after: ["2"] },
  distribution: { values: [1] }, // too few points
};

describe.each(Object.keys(GOOD) as GraphSpec["template"][])("%s", (template) => {
  test("draws an accessible picture with the same numbers as a hidden table", () => {
    render(<GraphView spec={spec(template, GOOD[template])} />);
    const svg = screen.getByRole("img");
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg).toHaveAttribute("aria-label", expect.stringContaining("Softmax weights by score"));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row").length).toBeGreaterThan(1);
    expect(screen.getByText("Higher scores take more of the weight.")).toBeInTheDocument();
  });

  test("draws nothing, and does not throw, when the data is malformed", () => {
    const { container } = render(<GraphView spec={spec(template, BAD[template])} />);
    expect(container).toBeEmptyDOMElement();
    expect(parseGraph(spec(template, BAD[template]))).toBeNull();
  });

  test("survives data of the wrong type altogether", () => {
    for (const data of [{}, { x: "nope" }, { values: "nope" }, { rows: 1, cols: 2 }]) {
      expect(() => render(<GraphView spec={spec(template, data)} />)).not.toThrow();
    }
  });
});

test("bar: one rect per value, the primary series in the accent colour", () => {
  const { container } = render(<GraphView spec={spec("bar", GOOD.bar)} />);
  const bars = container.querySelectorAll("rect");
  expect(bars).toHaveLength(3);
  bars.forEach((b) => expect(b).toHaveClass("fillAccent"));
});

test("line: the second series is a dashed ink line, and both are named in a legend", () => {
  const { container } = render(<GraphView spec={spec("line", GOOD.line)} />);
  const paths = container.querySelectorAll("path.line");
  expect(paths).toHaveLength(2);
  expect(paths[0]).toHaveClass("strokeAccent");
  expect(paths[1]).toHaveClass("strokeInk2", "dashA");
  expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("trainval");
});

test("heatmap: cells use opacity steps of the accent, and the darkest cell gets light text", () => {
  const { container } = render(<GraphView spec={spec("heatmap", GOOD.heatmap)} />);
  const cells = [...container.querySelectorAll("rect")];
  expect(cells).toHaveLength(6);
  const opacities = cells.map((c) => Number(c.getAttribute("fill-opacity")));
  expect(Math.max(...opacities)).toBeGreaterThan(Math.min(...opacities));
  cells.forEach((c) => expect(c).toHaveClass("fillAccent"));
  const texts = [...container.querySelectorAll("text.cellText")];
  expect(texts.find((t) => t.textContent === "0.8")).toHaveClass("cellOn");
  expect(texts.find((t) => t.textContent === "0.1")).toHaveClass("cellOff");
});

test("before/after: the 'after' series is the accent one", () => {
  const { container } = render(<GraphView spec={spec("before_after", GOOD.before_after)} />);
  const bars = [...container.querySelectorAll("rect")];
  expect(bars).toHaveLength(4);
  expect(bars[0]).toHaveClass("fillInk2");
  expect(bars[2]).toHaveClass("fillAccent");
  expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("BeforeAfter");
});

test("axis titles and legend names from the backend show up", () => {
  const { container } = render(
    <GraphView
      spec={spec("before_after", {
        ...GOOD.before_after,
        before_name: "naive",
        after_name: "stable",
        x_label: "score size",
        y_label: "weight",
      })}
    />,
  );
  expect(container.querySelectorAll("text.axisTitle")).toHaveLength(2);
  expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("naivestable");
});

test("line: numeric x values are placed by value, and a long line has no markers", () => {
  const xs = Array.from({ length: 120 }, (_v, i) => i * i);
  const { container } = render(<GraphView spec={spec("line", { x: xs, series: [{ name: "", y: xs.map((v) => v / 2) }] })} />);
  expect(container.querySelectorAll("circle")).toHaveLength(0);
  expect(container.querySelectorAll("path.line")).toHaveLength(1);
  // Unsorted x falls back to even spacing rather than drawing a scribble.
  expect(parseGraph(spec("line", { x: [3, 1, 2], series: [{ name: "a", y: [1, 2, 3] }] }))).toMatchObject({
    data: { xs: null },
  });
});

test("distribution: a curve is drawn as a line and bars as bars", () => {
  const curve = render(<GraphView spec={spec("distribution", { x: [0, 1, 2, 3], y: [0.1, 0.4, 0.4, 0.1], style: "curve" })} />);
  expect(curve.container.querySelectorAll("path.line")).toHaveLength(1);
  expect(curve.container.querySelectorAll("rect")).toHaveLength(0);
  curve.unmount();
  const bars = render(<GraphView spec={spec("distribution", GOOD.distribution)} />);
  expect(bars.container.querySelectorAll("rect")).toHaveLength(4);
});

test("up to six series are kept; extras beyond the third are faded copies of the palette", () => {
  const series = Array.from({ length: 7 }, (_v, i) => ({ name: `s${i}`, y: [i, i + 1] }));
  const { container } = render(<GraphView spec={spec("bar", { x: ["a", "b"], series })} />);
  expect(container.querySelectorAll("rect")).toHaveLength(12);
  expect(container.querySelectorAll("rect")[6]?.getAttribute("fill-opacity")).toBe("0.55");
});

test("distribution: raw values are binned into counts that add up", () => {
  const h = histogram([1, 2, 2, 3, 3, 3, 4, 4, 5, 9], 4);
  expect(h.series[0]?.y.reduce((a, b) => a + b, 0)).toBe(10);
  expect(h.x).toHaveLength(4);
  const direct = parseGraph(spec("distribution", { x: ["lo", "hi"], y: [3, 5] }));
  expect(direct?.kind).toBe("distribution");
});

test("scales: nice ranges include zero and formatting keeps three digits", () => {
  expect(niceRange(0, 0.66)).toMatchObject({ lo: 0, hi: 0.8 });
  expect(niceRange(-3, 7).ticks).toContain(0);
  expect(niceRange(0, 0)).toMatchObject({ lo: 0, hi: 1 });
  expect(fmt(0.659)).toBe("0.659");
  expect(fmt(0.66)).toBe("0.66");
  expect(fmt(1200)).toBe("1200");
  expect(fmt(1.2345678)).toBe("1.23");
  expect(fit("a-very-long-label", 30)).toMatch(/…$/);
  expect(fit("ab", 30)).toBe("ab");
});

describe("Graph tab inside the drawer", () => {
  const route = (graph: unknown, conceptGraph = false) => (url: string) => {
    if (paperUrl(url)) return json({ paper_id: PAPER, status: "ready", repo: null });
    if (url.includes("/explanation")) return json(EXPLANATION);
    if (url.includes("/graph")) return json(graph);
    if (url.includes(`/concepts/${CONCEPT}`)) return json(CONCEPT_PAYLOAD(conceptGraph));
    return undefined;
  };

  test("the graph is requested once the explanation is on screen, and the tab then appears", async () => {
    const calls = mockFetch(route(spec("bar", GOOD.bar)));
    renderDrawer();
    expect(calls.some((u) => u.includes("/graph"))).toBe(false);
    await screen.findByText("Every position looks at every other position.");
    const tab = await screen.findByRole("tab", { name: "Graph" });
    expect(calls.filter((u) => u.includes("/graph"))).toHaveLength(1);
    fireEvent.click(tab);
    expect(await screen.findByRole("img", { name: /^Bar chart\./ })).toBeInTheDocument();
    expect(calls.filter((u) => u.includes("/graph"))).toHaveLength(1); // the tab reuses the cached answer
  });

  test("a concept that gets no graph keeps the tab hidden", async () => {
    const calls = mockFetch(route({ template: "none" }));
    renderDrawer();
    await screen.findByText("Every position looks at every other position.");
    await waitFor(() => expect(calls.some((u) => u.includes("/graph"))).toBe(true));
    expect(screen.queryByRole("tab", { name: "Graph" })).toBeNull();
  });

  test("a graph that cannot be drawn keeps the tab hidden too", async () => {
    const calls = mockFetch(route(spec("heatmap", BAD.heatmap)));
    renderDrawer();
    await screen.findByText("Every position looks at every other position.");
    await waitFor(() => expect(calls.some((u) => u.includes("/graph"))).toBe(true));
    expect(screen.queryByRole("tab", { name: "Graph" })).toBeNull();
  });

  test("while the graph is still being computed (202) there is no tab and no error", async () => {
    mockFetch(route({ status: "generating" }, false));
    renderDrawer();
    await screen.findByText("Every position looks at every other position.");
    expect(screen.queryByRole("tab", { name: "Graph" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
