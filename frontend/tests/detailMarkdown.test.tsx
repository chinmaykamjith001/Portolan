import { render, waitFor } from "@testing-library/react";

import { anchorLabel } from "../src/detail/anchors";
import { collectAnchors, Markdown, parseBlocks, parseInline } from "../src/detail/markdown";

test.each([
  ["eq:1", "Eq. 1"],
  ["sec:3.2", "§3.2"],
  ["sec:3", "§3"],
  ["sec:A", "Appendix A"],
  ["sec:A.1", "§A.1"],
  ["tab:3", "Table 3"],
  ["fig:2", "Fig. 2"],
  ["alg:1", "Alg. 1"],
  ["para:3.2:2", "§3.2 ¶2"],
  ["para:front:1", "Front matter ¶1"],
  ["eq:3.2:u1", "Eq. in §3.2"],
  ["sec:3.scaled-dot-product", "Scaled dot product"],
  ["eq:4~2", "Eq. 4"],
  ["abstract", "Abstract"],
  ["weird", "weird"],
])("anchorLabel(%s) = %s", (id, label) => {
  expect(anchorLabel(id)).toBe(label);
});

test("blocks: paragraphs, headings, lists, display math", () => {
  const src = "## Idea\n\nFirst line\nsame paragraph.\n\n- one\n- two\n  continued\n\n$$\na = b\n$$\nAfter.";
  expect(parseBlocks(src)).toEqual([
    { kind: "h", level: 2, text: "Idea" },
    { kind: "p", text: "First line same paragraph." },
    { kind: "ul", items: ["one", "two continued"] },
    { kind: "math", tex: "a = b" },
    { kind: "p", text: "After." },
  ]);
});

test("inline: bold, italic, code, math, anchors", () => {
  expect(parseInline("**In one sentence.** Uses $QK^T$ and `x_i` (*see* [eq:1, sec:3.2]).")).toEqual([
    { t: "strong", c: [{ t: "text", v: "In one sentence." }] },
    { t: "text", v: " Uses " },
    { t: "math", v: "QK^T", display: false },
    { t: "text", v: " and " },
    { t: "code", v: "x_i" },
    { t: "text", v: " (" },
    { t: "em", c: [{ t: "text", v: "see" }] },
    { t: "text", v: " " },
    { t: "anchor", ids: ["eq:1", "sec:3.2"] },
    { t: "text", v: ")." },
  ]);
});

test("inline: currency, snake_case and plain brackets stay text", () => {
  expect(parseInline("costs $5 and $10")).toEqual([{ t: "text", v: "costs $5 and $10" }]);
  expect(parseInline("d_model and d_k")).toEqual([{ t: "text", v: "d_model and d_k" }]);
  expect(parseInline("a [note] here")).toEqual([{ t: "text", v: "a [note] here" }]);
  expect(parseInline("2 * 3 * 4")).toEqual([{ t: "text", v: "2 * 3 * 4" }]);
  expect(parseInline("\\$x\\$")).toEqual([{ t: "text", v: "$x$" }]);
});

test("collectAnchors finds refs in order without repeats", () => {
  expect(collectAnchors("See [eq:1].\n\n- and [tab:2]\n\n**[eq:1]**")).toEqual(["eq:1", "tab:2"]);
});

test("HTML in the source renders as text", () => {
  const { container } = render(<Markdown source={'<script>alert(1)</script> **<img src=x onerror="y">**'} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
});

test("math falls back to raw TeX, then renders with KaTeX", async () => {
  const { container, findByText } = render(<Markdown source="Score $e^{z_i}$ here." />);
  // Either still loading (raw TeX) or already rendered; both are fine first states.
  await findByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").includes("Score"));
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull(), { timeout: 5000 });
});
