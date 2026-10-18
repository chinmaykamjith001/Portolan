import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { keys } from "../src/api/client";
import type { Concept, Explanation, LayerPayload } from "../src/api/types";
import { DetailDrawer } from "../src/detail/DetailDrawer";
import { useUI } from "../src/state/store";

const PAPER = "arxiv:1706.03762v7";

function concept(id: string, title: string, extra: Partial<Concept> = {}): Concept {
  return {
    id,
    canonical_name: id,
    title,
    gist: `${title} gist.`,
    grounding: "in_code",
    is_first_principle: false,
    depth: 1,
    anchors: [],
    has_layer: false,
    gen_state: "skeleton",
    issue_count_self: 0,
    ...extra,
  };
}

const MASTER_LAYER: LayerPayload = {
  layer: {
    id: "master",
    paper_id: PAPER,
    center: "transformer",
    concepts: ["self_attention", "pe"],
    links: [],
    gen_state: "detailed",
  },
  concepts: {
    transformer: concept("transformer", "Transformer", { depth: 0, has_layer: true }),
    self_attention: concept("self_attention", "Self-attention", {
      anchors: ["sec:3.2", "eq:1"],
      has_layer: true,
    }),
    pe: concept("pe", "Positional encoding", { is_first_principle: true }),
  },
};

const EXPLANATION: Explanation = {
  concept_id: "self_attention",
  angle: "default",
  gist: "Every position looks at every other position.",
  sections: {
    what_it_is: "A way for each token to gather information from the others.",
    how_it_works: "Scores are $QK^T / \\sqrt{d_k}$, turned into weights by a softmax.",
    why_its_here: "It replaces recurrence in the Transformer.",
    where_it_appears: ["eq:1"],
    common_confusion: "It is not a hard lookup; every key keeps some weight [sec:3.2].",
  },
  body_markdown: "unused when sections are present",
  anchors_cited: ["eq:1"],
  model: "mock",
};

type Route = (url: string) => Response | Promise<Response>;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;

function setup(routes: Record<string, Route>) {
  fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    for (const [pattern, route] of Object.entries(routes)) {
      if (url.includes(pattern)) return Promise.resolve(route(url));
    }
    return new Promise<Response>(() => undefined); // anything not under test stays in flight
  });
  vi.stubGlobal("fetch", fetchMock);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(keys.layer(PAPER, "master"), MASTER_LAYER);
  act(() => {
    useUI.setState({ paperId: PAPER, view: "chart", path: [], selectedId: "self_attention", drawerOpen: true });
  });
  const utils = render(
    <QueryClientProvider client={qc}>
      <DetailDrawer />
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}

const never = () => new Promise<Response>(() => undefined);

afterEach(() => {
  vi.unstubAllGlobals();
  useUI.setState({ paperId: null, path: [], selectedId: null, drawerOpen: false, edge: null, cameFrom: null });
});

test("opens with the title from the cached layer before the concept request returns", () => {
  setup({ "/concepts/self_attention": never, [`/papers/${encodeURIComponent(PAPER)}`]: never });
  const title = screen.getByRole("heading", { level: 2, name: "Self-attention" });
  expect(title).toBeInTheDocument();
  expect(document.activeElement).toBe(title);
  expect(screen.getByText("Transformer")).toBeInTheDocument(); // "On the Transformer map"
  expect(screen.getByText("§3.2")).toBeInTheDocument();
  expect(screen.getByText("Eq. 1")).toBeInTheDocument();
  expect(screen.getByText("In the code")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Explain" })).toHaveAttribute("aria-selected", "true");
});

test("a 202 explanation shows reserved skeleton lines", async () => {
  setup({ "/explanation": () => json({ status: "generating" }, 202), "/concepts/self_attention": never });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("angle=default"), expect.anything()));
  const region = document.querySelector('[aria-live="polite"]');
  expect(region).toHaveAttribute("aria-busy", "true");
  expect(region?.querySelectorAll("span").length).toBeGreaterThan(4);
});

test("a ready explanation renders gist, sections, anchors and math", async () => {
  const { container } = setup({ "/explanation": () => json(EXPLANATION), "/concepts/self_attention": never });
  expect(await screen.findByText("Every position looks at every other position.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "How it works" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Where it appears" })).toBeInTheDocument();
  // [sec:3.2] in the text becomes a readable label inline and a chip at the end
  expect(screen.getAllByText("§3.2").length).toBeGreaterThanOrEqual(2);
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull(), { timeout: 5000 });
  expect(document.querySelector('[aria-live="polite"]')).toHaveAttribute("aria-busy", "false");
});

test("switching angle requests that angle", async () => {
  setup({ "/explanation": () => json({ status: "generating" }, 202), "/concepts/self_attention": never });
  fireEvent.click(screen.getByRole("button", { name: "Simpler" }));
  expect(screen.getByRole("button", { name: "Simpler" })).toHaveAttribute("aria-pressed", "true");
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/concepts/self_attention/explanation?angle=simpler"),
      expect.anything(),
    ),
  );
});

test("an explanation error shows a plain sentence and a retry", async () => {
  let calls = 0;
  setup({
    "/explanation": () => {
      calls += 1;
      return calls === 1
        ? json({ error: { code: "generation_failed", message: "Couldn't write this explanation. Try again." } }, 502)
        : json(EXPLANATION);
    },
    "/concepts/self_attention": never,
  });
  expect(await screen.findByText("Couldn't write this explanation. Try again.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Every position looks at every other position.")).toBeInTheDocument();
});

test("Jump in is offered for a concept with a layer and goes one level down", () => {
  setup({ "/explanation": never, "/concepts/self_attention": never });
  fireEvent.click(screen.getByRole("button", { name: "Jump in" }));
  expect(useUI.getState().path).toEqual(["self_attention"]);
  expect(useUI.getState().drawerOpen).toBe(false);
});

test("Code tab says plainly when the paper has no code", async () => {
  setup({
    "/explanation": never,
    "/concepts/self_attention": never,
    [`/papers/${encodeURIComponent(PAPER)}`]: () => json({ paper_id: PAPER, status: "ready", repo: null }),
  });
  fireEvent.click(screen.getByRole("tab", { name: "Code" }));
  expect(await screen.findByText("No code linked for this paper.")).toBeInTheDocument();
});

test("the center concept and first principles have no Jump in", () => {
  setup({ "/explanation": never, "/concepts/": never });
  act(() => useUI.setState({ selectedId: "pe" }));
  expect(screen.queryByRole("button", { name: "Jump in" })).toBeNull();
  act(() => useUI.setState({ selectedId: "transformer" }));
  expect(screen.queryByRole("button", { name: "Jump in" })).toBeNull();
});
