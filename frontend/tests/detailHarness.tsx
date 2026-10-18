// Shared setup for the drawer-tab tests: a drawer open on "Self-attention" with fetch routed by URL.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactElement } from "react";

import { keys } from "../src/api/client";
import type { CodeGrounding, Concept, ConceptPayload, Explanation, LayerPayload } from "../src/api/types";
import { DetailDrawer } from "../src/detail/DetailDrawer";
import { useUI } from "../src/state/store";

export const PAPER = "arxiv:1706.03762v7";
export const CONCEPT = "self_attention";

export function concept(id: string, title: string, extra: Partial<Concept> = {}): Concept {
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
    gen_state: "detailed",
    issue_count_self: 0,
    ...extra,
  };
}

export const MASTER_LAYER: LayerPayload = {
  layer: {
    id: "master",
    paper_id: PAPER,
    center: "transformer",
    concepts: [CONCEPT],
    links: [],
    gen_state: "detailed",
  },
  concepts: {
    transformer: concept("transformer", "Transformer", { depth: 0, has_layer: true }),
    [CONCEPT]: concept(CONCEPT, "Self-attention", { anchors: ["eq:1"] }),
  },
};

export const EXPLANATION: Explanation = {
  concept_id: CONCEPT,
  angle: "default",
  gist: "Every position looks at every other position.",
  sections: {
    what_it_is: "A way for each token to gather information from the others.",
    how_it_works: "Scores become weights through a softmax.",
    why_its_here: "It replaces recurrence.",
    where_it_appears: ["eq:1"],
    common_confusion: "It is not a hard lookup.",
  },
  body_markdown: "unused",
  anchors_cited: ["eq:1"],
  model: "mock",
};

export const CONCEPT_PAYLOAD = (graph = false): ConceptPayload => ({
  concept: MASTER_LAYER.concepts[CONCEPT] as Concept,
  also_appears_in: [],
  issues_inside: 0,
  details: { explanation: true, code: false, example: false, graph },
});

const FILE = "toyformer/attention.py";
const LINES: Record<string, string> = {
  "38": "def attention(q, k, v, mask=None):",
  "39": "    # scores for every query-key pair",
  "40": "    scores = q @ k.transpose(-2, -1) / math.sqrt(64)",
  "41": "    if mask is not None:",
  "42": "        scores = scores.masked_fill(mask == 0, -1e9)",
  "43": "    weights = torch.softmax(scores, dim=-1)",
  "44": "    return weights @ v",
};

export const GROUNDING: CodeGrounding = {
  concept_id: CONCEPT,
  grounding: "in_code",
  spans: [{ repo_id: "r", commit_sha: "abc123", path: FILE, start_line: 38, end_line: 44 }],
  notes: [
    { line: 40, tag: "Eq. 1 · QKᵀ/√dₖ", note: "Scores every query against every key.", kind: "implements" },
    { line: 42, tag: "masking", note: "Hides padding positions before the softmax.", kind: "detail_not_in_paper" },
    { line: 43, tag: "Eq. 1", note: "Divides by 64, but the paper's d_k is 512.", kind: "mismatch" },
    { line: 44, tag: "Eq. 1", note: "Mixes the values with the weights.", kind: "context" },
  ],
  library_calls: ["torch.softmax"],
  summary: "Lines 38–44 implement Eq. 1.",
  code: { [FILE]: LINES },
  links: { [FILE]: "https://github.com/o/r/blob/abc123/toyformer/attention.py" },
};

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export type Router = (url: string) => Response | Promise<Response> | undefined;

export function mockFetch(router: Router) {
  const calls: string[] = [];
  const fn = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    return Promise.resolve(router(url) ?? new Response("{}", { status: 404 }));
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export const paperUrl = (url: string) => url.endsWith(`/api/papers/${encodeURIComponent(PAPER)}`);

export function newClient(): QueryClient {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(keys.layer(PAPER, "master"), MASTER_LAYER);
  return qc;
}

export function renderWithClient(ui: ReactElement, qc = newClient()) {
  return { qc, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

/** A drawer open on Self-attention. */
export function renderDrawer(qc = newClient()) {
  act(() => {
    useUI.setState({ paperId: PAPER, view: "chart", path: [], selectedId: CONCEPT, drawerOpen: true });
  });
  return renderWithClient(<DetailDrawer />, qc);
}
