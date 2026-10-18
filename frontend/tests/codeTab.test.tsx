import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { keys } from "../src/api/client";
import type { CodeGrounding } from "../src/api/types";
import { buildBlocks } from "../src/detail/code";
import { CodeTab } from "../src/detail/CodeTab";
import { tokenize } from "../src/detail/highlight";
import {
  CONCEPT,
  CONCEPT_PAYLOAD,
  EXPLANATION,
  GROUNDING,
  PAPER,
  json,
  mockFetch,
  newClient,
  paperUrl,
  renderDrawer,
  renderWithClient,
} from "./detailHarness";

const noop = () => undefined;

function renderCode(g: CodeGrounding | null, props: { hasRepo?: boolean | null; onShowExample?: () => void } = {}) {
  const qc = newClient();
  if (g) qc.setQueryData(keys.code(PAPER, CONCEPT), g);
  return renderWithClient(
    <CodeTab
      paperId={PAPER}
      conceptId={CONCEPT}
      hasRepo={"hasRepo" in props ? (props.hasRepo ?? null) : true}
      onShowExample={props.onShowExample ?? noop}
    />,
    qc,
  );
}

describe("tokenizer", () => {
  test("pieces always put the line back together", () => {
    const lines = [
      "def attention(q, k, v, mask=None):",
      "    scores = q @ k.T / math.sqrt(64)  # scale",
      '    name = f"layer_{i}"',
      "d_model: 512  # width",
      "lr = 1e-3",
      "",
    ];
    for (const lang of ["python", "config", "plain"] as const) {
      for (const l of lines) expect(tokenize(l, lang).map((t) => t.text).join("")).toBe(l);
    }
  });

  test("marks keywords, definitions, numbers, strings and comments in Python", () => {
    const kinds = Object.fromEntries(
      tokenize('def attn(x): return "a" + 1.5  # why', "python").map((t) => [t.text.trim(), t.kind]),
    );
    expect(kinds).toMatchObject({ def: "kw", attn: "def", return: "kw", '"a"': "str", "1.5": "num", "# why": "com" });
  });

  test("marks config keys and values", () => {
    const kinds = Object.fromEntries(tokenize("  d_model: 512  # width", "config").map((t) => [t.text.trim(), t.kind]));
    expect(kinds).toMatchObject({ d_model: "key", "512": "num", "# width": "com" });
  });
});

describe("blocks", () => {
  test("each note lands on its own line once, even when spans overlap", () => {
    const g: CodeGrounding = {
      ...GROUNDING,
      spans: [
        { repo_id: "r", commit_sha: "abc123", path: "toyformer/attention.py", start_line: 38, end_line: 42 },
        { repo_id: "r", commit_sha: "abc123", path: "toyformer/attention.py", start_line: 40, end_line: 44 },
      ],
    };
    const blocks = buildBlocks(g);
    const placed = blocks.flatMap((b) => b.lines.flatMap((l) => l.notes.map((n) => n.line)));
    expect(placed.sort()).toEqual([40, 42, 43, 44]);
    expect(blocks[0]?.href).toBe("https://github.com/o/r/blob/abc123/toyformer/attention.py#L38-L42");
  });

  test("a span whose text isn't available is skipped", () => {
    expect(buildBlocks({ ...GROUNDING, code: {} })).toEqual([]);
  });
});

describe("Code tab", () => {
  test("puts one arrow callout directly under each annotated line", () => {
    renderCode(GROUNDING);
    const callouts = screen.getAllByRole("note");
    expect(callouts).toHaveLength(4);
    const lines = callouts.map((c) => c.previousElementSibling?.getAttribute("data-line"));
    expect(lines).toEqual(["40", "42", "43", "44"]);
    expect(within(callouts[0] as HTMLElement).getByText("Eq. 1 · QKᵀ/√dₖ").tagName).toBe("STRONG");
  });

  test("shows the file as a link at the commit, with the summary and library calls", () => {
    renderCode(GROUNDING);
    const link = screen.getByRole("link", { name: "toyformer/attention.py" });
    expect(link).toHaveAttribute("href", "https://github.com/o/r/blob/abc123/toyformer/attention.py#L38-L44");
    expect(screen.getByText("Lines 38–44 implement Eq. 1.")).toBeInTheDocument();
    expect(screen.getByText("torch.softmax")).toBeInTheDocument();
    expect(screen.getByText("Lines 38–44")).toBeInTheDocument();
  });

  test("highlights annotated lines and dims the rest", () => {
    renderCode(GROUNDING);
    const region = screen.getByRole("region", { name: /toyformer\/attention\.py, lines 38–44/ });
    expect(region).toHaveClass("hasNotes");
    const row = (n: number) => region.querySelector(`[data-line="${n}"]`);
    expect(row(40)).toHaveClass("rowNoted");
    expect(row(42)).toHaveClass("rowNoted");
    expect(row(43)).toHaveClass("rowMismatch");
    expect(row(44)).toHaveClass("rowContext");
    expect(row(39)).not.toHaveClass("rowNoted", "rowMismatch", "rowContext");
  });

  test("a mismatch carries a ! glyph and plain-word text, not just a colour", () => {
    renderCode(GROUNDING);
    const [, , mismatch] = screen.getAllByRole("note");
    expect(mismatch).toHaveAttribute("data-kind", "mismatch");
    expect(within(mismatch as HTMLElement).getByText("!")).toBeInTheDocument();
    expect(within(mismatch as HTMLElement).getByText("Mismatch:")).toBeInTheDocument();
    // The other kinds get no "!" and say what they are for screen readers.
    const [implementsNote, detail, , context] = screen.getAllByRole("note");
    expect(within(implementsNote as HTMLElement).queryByText("!")).toBeNull();
    expect(within(detail as HTMLElement).getByText("Not in the paper:")).toBeInTheDocument();
    expect(detail).toHaveAttribute("data-kind", "detail_not_in_paper");
    expect(context).toHaveAttribute("data-kind", "context");
  });

  test("concept_only explains itself and offers the Example tab", () => {
    const onShowExample = vi.fn();
    renderCode({ ...GROUNDING, grounding: "concept_only", spans: [], notes: [], library_calls: [], code: {} }, { onShowExample });
    expect(screen.getByText(/isn't spelled out in the code/)).toBeInTheDocument();
    expect(screen.queryByRole("note")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "See an example" }));
    expect(onShowExample).toHaveBeenCalledOnce();
  });

  test("a library-only concept names the call instead of showing lines", () => {
    renderCode({ ...GROUNDING, grounding: "in_library", spans: [], notes: [], code: {}, summary: "Done by torch." });
    expect(screen.getByText("Done by torch.")).toBeInTheDocument();
    expect(screen.getByText("torch.softmax")).toBeInTheDocument();
    expect(screen.queryByRole("region")).toBeNull();
  });

  test("with no repo it says so and makes no request", () => {
    const calls = mockFetch(() => undefined);
    renderCode(null, { hasRepo: false });
    expect(screen.getByText("No code linked for this paper.")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  test("while the paper is loading it waits without a request", () => {
    const calls = mockFetch(() => undefined);
    renderCode(null, { hasRepo: null });
    expect(calls).toEqual([]);
    expect(document.querySelector("[aria-busy=true]")).not.toBeNull();
  });

  test("a failed request shows the backend's sentence and retries", async () => {
    let attempts = 0;
    mockFetch((url) => {
      if (!url.includes("/code")) return undefined;
      attempts++;
      return attempts === 1
        ? json({ error: { code: "generation_failed", message: "Couldn't write this part. Try again." } }, 502)
        : json(GROUNDING);
    });
    renderCode(null);
    expect(await screen.findByText("Couldn't write this part. Try again.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Lines 38–44 implement Eq. 1.")).toBeInTheDocument();
  });

  test("a pending (202) answer shows reserved skeleton lines", async () => {
    mockFetch((url) => (url.includes("/code") ? json({ status: "generating" }, 202) : undefined));
    renderCode(null);
    await waitFor(() => expect(document.querySelector("[aria-busy=true]")).not.toBeNull());
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("Code tab inside the drawer", () => {
  test("code is requested only when the tab is opened, and only when a repo is linked", async () => {
    const calls = mockFetch((url) => {
      if (paperUrl(url)) return json({ paper_id: PAPER, status: "ready", repo: { id: "r" } });
      if (url.includes("/explanation")) return json(EXPLANATION);
      if (url.includes("/code")) return json(GROUNDING);
      if (url.includes("/graph")) return json({ template: "none" });
      if (url.includes(`/concepts/${CONCEPT}`)) return json(CONCEPT_PAYLOAD());
      return undefined;
    });
    renderDrawer();
    await screen.findByText("Every position looks at every other position.");
    expect(calls.some((u) => u.includes("/code"))).toBe(false);
    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    expect(await screen.findByText("Lines 38–44 implement Eq. 1.")).toBeInTheDocument();
    expect(calls.filter((u) => u.includes("/code"))).toHaveLength(1);
  });

  test("the concept_only button switches to the Example tab", async () => {
    mockFetch((url) => {
      if (paperUrl(url)) return json({ paper_id: PAPER, status: "ready", repo: { id: "r" } });
      if (url.includes("/code")) return json({ ...GROUNDING, grounding: "concept_only", spans: [], notes: [], code: {} });
      if (url.includes("/example")) return json({ status: "generating" }, 202);
      if (url.includes(`/concepts/${CONCEPT}`)) return json(CONCEPT_PAYLOAD());
      return new Promise<Response>(() => undefined);
    });
    renderDrawer();
    fireEvent.click(screen.getByRole("tab", { name: "Code" }));
    fireEvent.click(await screen.findByRole("button", { name: "See an example" }));
    expect(screen.getByRole("tab", { name: "Example" })).toHaveAttribute("aria-selected", "true");
  });
});
