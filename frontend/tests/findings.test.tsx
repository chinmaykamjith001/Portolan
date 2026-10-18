import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import { PaperStrip } from "../src/app/PaperStrip";
import type { Finding, FindingsPayload, PaperInfo } from "../src/api/types";
import { FindingsPanel } from "../src/findings/FindingsPanel";
import {
  conceptVerdict,
  confidenceWords,
  isProminent,
  sortFindings,
  spanHref,
  summarize,
} from "../src/findings/model";
import { useUI } from "../src/state/store";
import { CONCEPT, GROUNDING, json, mockFetch, newClient, PAPER, renderDrawer, renderWithClient } from "./detailHarness";

const SHA = "a".repeat(40);

function finding(id: string, over: Partial<Finding> = {}): Finding {
  return {
    id,
    claim_text: `Claim ${id}`,
    claim_type: "hyperparameter",
    paper_anchor: "sec:3.2.1",
    code_spans: [],
    verdict: "mismatch",
    severity: "medium",
    confidence: 0.9,
    method: "literal_match",
    evidence: `Evidence ${id}: the paper says 512, the code says 256.`,
    concept_ids: [],
    ...over,
  };
}

const MIXED: Finding[] = [
  finding("ok", { verdict: "matches", severity: "low", confidence: 0.95, concept_ids: [CONCEPT] }),
  finding("maybe", { verdict: "uncertain", severity: "low", confidence: 0.5 }),
  finding("extra", { verdict: "undocumented_in_paper", severity: "low", confidence: 0.8 }),
  finding("bad", { verdict: "mismatch", severity: "high", confidence: 0.92, concept_ids: ["layer_norm"] }),
  finding("worse", {
    verdict: "mismatch",
    severity: "high",
    confidence: 0.99,
    concept_ids: [CONCEPT],
    code_spans: [{ repo_id: "r", commit_sha: SHA, path: "toyformer/layers.py", start_line: 12, end_line: 14 }],
  }),
];

const payload = (state: FindingsPayload["state"], findings: Finding[] = MIXED): FindingsPayload => ({ findings, state });

const REPO = {
  id: "r",
  url: "https://github.com/o/r",
  owner: "o",
  name: "r",
  official: true,
  official_evidence: null,
  status: "ok" as const,
  stars: null,
};
const PAPER_INFO: PaperInfo = { paper_id: PAPER, status: "ready", title: "T", repo: REPO, chart_ready: true };

function routes(f: FindingsPayload, paper: PaperInfo = PAPER_INFO) {
  return mockFetch((url) => {
    if (url.endsWith("/findings")) return json(f);
    if (url.endsWith(`/api/papers/${encodeURIComponent(PAPER)}`)) return json(paper);
    return undefined;
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  useUI.setState({ paperId: PAPER, view: "chart", findingsOpen: false, pendingFocus: null, selectedId: null, drawerOpen: false });
});

describe("model", () => {
  test("sorts mismatches first, then severity, then confidence", () => {
    expect(sortFindings(MIXED).map((f) => f.id)).toEqual(["worse", "bad", "extra", "maybe", "ok"]);
  });

  test("only mismatch and undocumented are prominent", () => {
    expect(MIXED.filter(isProminent).map((f) => f.id).sort()).toEqual(["bad", "extra", "worse"]);
  });

  test("confidence in plain words", () => {
    expect(confidenceWords(0.85)).toBe("high confidence");
    expect(confidenceWords(0.7)).toBe("medium confidence");
    expect(confidenceWords(0.69)).toBe("low confidence");
  });

  test("summary wording for every state", () => {
    expect(summarize(payload("done", [finding("a")]), true)?.text).toBe("1 mismatch");
    expect(summarize(payload("done", [finding("a"), finding("b")]), true)?.text).toBe("2 mismatches");
    expect(summarize(payload("done", []), true)?.text).toBe("No mismatches found");
    expect(summarize(payload("running", []), true)?.text).toBe("Checking the code…");
    expect(summarize(payload("running", [finding("a")]), true)?.text).toBe("1 mismatch so far, still checking");
    expect(summarize(payload("failed", []), true)?.text).toBe("Couldn't finish checking");
    expect(summarize(payload("none", []), true)).toBeNull();
    expect(summarize(payload("done", [finding("a")]), false)).toBeNull(); // no code linked
    expect(summarize(undefined, true)).toBeNull();
  });

  test("a mismatch wins the concept's verdict; uncertain stays quiet", () => {
    const both = [
      finding("m", { verdict: "matches", concept_ids: ["c"] }),
      finding("x", { verdict: "mismatch", concept_ids: ["c"], evidence: "paper 512, code 256" }),
    ];
    expect(conceptVerdict(both, "c")).toMatchObject({ kind: "mismatch", evidence: "paper 512, code 256" });
    expect(conceptVerdict([finding("m", { verdict: "matches", concept_ids: ["c"] })], "c")?.kind).toBe("matches");
    expect(conceptVerdict([finding("u", { verdict: "undocumented_in_paper", concept_ids: ["c"] })], "c")?.kind).toBe(
      "undocumented",
    );
    expect(conceptVerdict([finding("q", { verdict: "uncertain", concept_ids: ["c"] })], "c")).toBeNull();
    expect(conceptVerdict(both, "other")).toBeNull();
    expect(conceptVerdict(undefined, "c")).toBeNull();
  });

  test("span links only for GitHub and GitLab at a full commit", () => {
    expect(spanHref("https://github.com/o/r", SHA, "a/b.py", 3, 5)).toBe(`https://github.com/o/r/blob/${SHA}/a/b.py#L3-L5`);
    expect(spanHref("https://github.com/o/r.git/", SHA, "a.py", 3, 3)).toBe(`https://github.com/o/r/blob/${SHA}/a.py#L3`);
    expect(spanHref("https://gitlab.com/o/r", SHA, "a.py", 1, 2)).toBe(`https://gitlab.com/o/r/-/blob/${SHA}/a.py#L1-L2`);
    expect(spanHref("https://example.com/o/r", SHA, "a.py", 1, 2)).toBeNull();
    expect(spanHref("https://github.com/o/r", "abc123", "a.py", 1, 2)).toBeNull();
    expect(spanHref(null, SHA, "a.py", 1, 2)).toBeNull();
  });
});

describe("FindingsPanel", () => {
  async function openPanel(f: FindingsPayload) {
    routes(f);
    useUI.setState({ findingsOpen: true });
    return renderWithClient(<FindingsPanel />);
  }

  test("renders nothing while closed", () => {
    routes(payload("done"));
    renderWithClient(<FindingsPanel />);
    expect(screen.queryByRole("dialog", { name: "Findings" })).toBeNull();
  });

  test("lists mismatches and undocumented behavior first, the rest behind a toggle", async () => {
    await openPanel(payload("done"));
    const list = await screen.findByRole("list");
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("data-verdict"))).toEqual(["mismatch", "mismatch", "undocumented_in_paper"]);
    expect(within(rows[0] as HTMLElement).getByText("Claim worse")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText(/Evidence worse/)).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText("high confidence")).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText("§3.2.1")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Show 2 lower-confidence and matching checks" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Hide lower-confidence and matching checks" }));
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(3);
  });

  test("code spans link to the repo at the commit", async () => {
    await openPanel(payload("done"));
    const link = await screen.findByRole("link", { name: "toyformer/layers.py:12–14" });
    expect(link).toHaveAttribute("href", `https://github.com/o/r/blob/${SHA}/toyformer/layers.py#L12-L14`);
  });

  test("Show on Chart asks the chart to reveal the first concept and closes the panel", async () => {
    await openPanel(payload("done"));
    const buttons = await screen.findAllByRole("button", { name: "Show on Chart" });
    fireEvent.click(buttons[0] as HTMLElement); // "worse" → CONCEPT
    expect(useUI.getState().pendingFocus).toEqual({ conceptId: CONCEPT, view: "chart" });
    expect(useUI.getState().findingsOpen).toBe(false);
  });

  test("no mismatches", async () => {
    await openPanel(payload("done", [finding("ok", { verdict: "matches" })]));
    expect(await screen.findByText("No mismatches found")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.getByText(/routine/)).toBeInTheDocument();
  });

  test("running, failed and a load error", async () => {
    await openPanel(payload("running", []));
    expect(await screen.findByText("Checking the code…")).toBeInTheDocument();
  });

  test("failed run", async () => {
    await openPanel(payload("failed", []));
    expect(await screen.findByText("Couldn't finish checking")).toBeInTheDocument();
  });

  test("a server error says so in plain words", async () => {
    mockFetch((url) => (url.endsWith("/findings") ? json({ error: { code: "x", message: "boom" } }, 500) : undefined));
    useUI.setState({ findingsOpen: true });
    renderWithClient(<FindingsPanel />);
    expect(await screen.findByText(/Couldn't load the findings/)).toBeInTheDocument();
  });

  test("Esc through the store closes the panel before anything else", async () => {
    await openPanel(payload("done"));
    await screen.findByRole("list");
    act(() => useUI.setState({ drawerOpen: true, selectedId: "x" }));
    act(() => useUI.getState().escape());
    expect(useUI.getState().findingsOpen).toBe(false);
    expect(useUI.getState().drawerOpen).toBe(true);
  });

  test("it is a labelled dialog with a close button", async () => {
    await openPanel(payload("done"));
    const dialog = await screen.findByRole("dialog", { name: "Findings" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close findings" }));
    expect(useUI.getState().findingsOpen).toBe(false);
  });
});

describe("PaperStrip findings control", () => {
  const live = { progress: null, rateLimitedUntil: null, lastError: null };

  test("shows the mismatch count and opens the panel", async () => {
    routes(payload("done", [finding("a")]));
    renderWithClient(<PaperStrip paper={PAPER_INFO} live={live} />);
    const button = await screen.findByRole("button", { name: "Findings: 1 mismatch" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(useUI.getState().findingsOpen).toBe(true);
  });

  test("says it is checking while the run is going", async () => {
    routes(payload("running", []));
    renderWithClient(<PaperStrip paper={PAPER_INFO} live={live} />);
    expect(await screen.findByRole("button", { name: "Findings: Checking the code…" })).toBeInTheDocument();
  });

  test("nothing, and no request, when no code is linked", async () => {
    const calls = routes(payload("done", [finding("a")]), { ...PAPER_INFO, repo: null });
    renderWithClient(<PaperStrip paper={{ ...PAPER_INFO, repo: null }} live={live} />);
    await waitFor(() => expect(screen.getByText("T")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Findings/ })).toBeNull();
    expect(calls.some((u) => u.endsWith("/findings"))).toBe(false);
  });
});

describe("drawer verdict", () => {
  function withFindings(f: Finding[]) {
    return mockFetch((url) => {
      if (url.endsWith("/findings")) return json(payload("done", f));
      if (url.endsWith(`/api/papers/${encodeURIComponent(PAPER)}`)) return json(PAPER_INFO);
      if (url.includes("/code")) return json({ ...GROUNDING, spans: [{ ...GROUNDING.spans[0] }] });
      return undefined;
    });
  }

  test("header says Mismatch when a mismatch names the concept", async () => {
    withFindings(MIXED);
    renderDrawer(newClient());
    expect(await screen.findByText("! Mismatch")).toBeInTheDocument();
  });

  test("header says Matches paper when only matches are attached", async () => {
    withFindings([MIXED[0] as Finding]);
    renderDrawer(newClient());
    expect(await screen.findByText("Matches paper")).toBeInTheDocument();
  });

  test("nothing when no finding names the concept", async () => {
    withFindings([finding("elsewhere", { concept_ids: ["layer_norm"] })]);
    renderDrawer(newClient());
    await screen.findByText("Self-attention");
    await waitFor(() => expect(screen.queryByText(/Mismatch|Matches paper/)).toBeNull());
  });

  test("the Code tab ends with a verdict box carrying the evidence", async () => {
    withFindings(MIXED);
    renderDrawer(newClient());
    fireEvent.click(await screen.findByRole("tab", { name: "Code" }));
    const box = await screen.findByText("The code differs from the paper here.");
    expect(box.closest("[role=note]")).toHaveTextContent(/Evidence worse/);
  });
});
