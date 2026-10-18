import type { FAQItem } from "../src/api/types";
import { filterItems, kindCounts, mergeItems, safeUrl, sourceLabel } from "../src/faq/faq";

const item = (id: string, source_kind: FAQItem["source_kind"], extra: Partial<FAQItem> = {}): FAQItem => ({
  id,
  question: `Question ${id}?`,
  answer_markdown: "Answer.",
  source_kind,
  source_url: null,
  source_label: "",
  concept_ids: [],
  anchors_cited: [],
  supported: true,
  ...extra,
});

test("kindCounts lists only the kinds present, in a stable order, with counts", () => {
  const items = [item("1", "anticipated"), item("2", "github_issue"), item("3", "anticipated")];
  expect(kindCounts(items)).toEqual([
    { kind: "github_issue", label: "GitHub issues", count: 1 },
    { kind: "anticipated", label: "Anticipated", count: 2 },
  ]);
});

test("filterItems keeps one kind or everything", () => {
  const items = [item("1", "anticipated"), item("2", "finding")];
  expect(filterItems(items, "all")).toHaveLength(2);
  expect(filterItems(items, "finding").map((i) => i.id)).toEqual(["2"]);
});

test("mergeItems puts session answers first and drops repeats", () => {
  const stored = [item("a", "anticipated"), item("b", "user_asked")];
  const asked = [item("b", "user_asked"), item("c", "user_asked")];
  expect(mergeItems(asked, stored).map((i) => i.id)).toEqual(["b", "c", "a"]);
});

test("safeUrl only follows web links", () => {
  expect(safeUrl("https://github.com/o/r/issues/3")).toBe("https://github.com/o/r/issues/3");
  expect(safeUrl("javascript:alert(1)")).toBeNull();
  expect(safeUrl("data:text/html,<b>x</b>")).toBeNull();
  expect(safeUrl("not a url")).toBeNull();
  expect(safeUrl(null)).toBeNull();
});

test("sourceLabel falls back to a plain label", () => {
  expect(sourceLabel(item("1", "github_issue", { source_label: "From GitHub issue #12" }))).toBe(
    "From GitHub issue #12",
  );
  expect(sourceLabel(item("2", "reviewer_discussion"))).toBe("From reviewer discussion");
});
