// Pure helpers for the FAQ view (spec §13.8): source-kind labels, filtering and link safety.

import type { FAQItem, FaqSource } from "../api/types";

export const KIND_ORDER: FaqSource[] = [
  "reviewer_discussion",
  "github_issue",
  "paper_page_discussion",
  "finding",
  "anticipated",
  "user_asked",
];

export const KIND_LABEL: Record<FaqSource, string> = {
  reviewer_discussion: "Reviewer discussion",
  github_issue: "GitHub issues",
  paper_page_discussion: "Paper page",
  finding: "Findings",
  anticipated: "Anticipated",
  user_asked: "Your questions",
};

/** Label shown under an answer when the backend sent none. */
const ITEM_LABEL: Record<FaqSource, string> = {
  reviewer_discussion: "From reviewer discussion",
  github_issue: "From a GitHub issue",
  paper_page_discussion: "From the paper page",
  finding: "Finding",
  anticipated: "Anticipated",
  user_asked: "You asked",
};

export function sourceLabel(item: FAQItem): string {
  return item.source_label.trim() || ITEM_LABEL[item.source_kind];
}

/** Only web links are followed; anything else (javascript:, data:) is shown as plain text. */
export function safeUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export interface KindCount {
  kind: FaqSource;
  label: string;
  count: number;
}

/** The kinds that occur, in a stable order, with counts. */
export function kindCounts(items: FAQItem[]): KindCount[] {
  const counts = new Map<FaqSource, number>();
  for (const i of items) counts.set(i.source_kind, (counts.get(i.source_kind) ?? 0) + 1);
  return KIND_ORDER.filter((k) => counts.has(k)).map((kind) => ({
    kind,
    label: KIND_LABEL[kind],
    count: counts.get(kind) ?? 0,
  }));
}

export function filterItems(items: FAQItem[], kind: FaqSource | "all"): FAQItem[] {
  return kind === "all" ? items : items.filter((i) => i.source_kind === kind);
}

/** Answers asked in this session come first (newest first), then the stored items, no repeats. */
export function mergeItems(asked: FAQItem[], stored: FAQItem[]): FAQItem[] {
  const seen = new Set(asked.map((i) => i.id));
  return [...asked, ...stored.filter((i) => !seen.has(i.id))];
}
