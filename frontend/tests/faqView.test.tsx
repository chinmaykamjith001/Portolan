import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { FAQItem } from "../src/api/types";
import FaqView from "../src/faq/FaqView";
import { useUI } from "../src/state/store";
import { PAPER, json, mockFetch, renderWithClient } from "./detailHarness";

const item = (id: string, kind: FAQItem["source_kind"], extra: Partial<FAQItem> = {}): FAQItem => ({
  id,
  question: `Why ${id}?`,
  answer_markdown: `Because of ${id}.`,
  source_kind: kind,
  source_url: null,
  source_label: "",
  concept_ids: [],
  anchors_cited: [],
  supported: true,
  ...extra,
});

const ITEMS: FAQItem[] = [
  item("scaling", "reviewer_discussion", {
    source_label: "From reviewer discussion",
    source_url: "https://openreview.net/forum?id=abc",
    concept_ids: ["self_attention"],
    anchors_cited: ["eq:1", "sec:3.2"],
  }),
  item("mask", "github_issue", {
    source_label: "From GitHub issue #12",
    source_url: "https://github.com/o/r/issues/12",
  }),
  item("norm", "finding", { source_label: "Finding", concept_ids: ["transformer"] }),
  item("lr", "anticipated", { source_label: "Anticipated" }),
  item("nope", "user_asked", { supported: false, source_label: "You asked", concept_ids: ["self_attention"] }),
];

const click = (el: Element) => act(() => void fireEvent.click(el));
/** Type into the ask box and submit the form (what Enter does). */
function ask(box: HTMLElement, text: string) {
  fireEvent.change(box, { target: { value: text } });
  fireEvent.submit(screen.getByRole("search", { name: "Ask about this paper" }));
}

const faqUrl = (url: string) => url.endsWith(`/api/papers/${encodeURIComponent(PAPER)}/faq`);
const askUrl = (url: string) => url.endsWith(`/api/papers/${encodeURIComponent(PAPER)}/ask`);

function open() {
  act(() => {
    useUI.setState({ paperId: PAPER, view: "faq", pendingFocus: null });
  });
  return renderWithClient(<FaqView />);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows a reserved loading state while the first build runs", async () => {
  mockFetch((url) => (faqUrl(url) ? new Response("{}", { status: 202 }) : undefined));
  open();
  expect(await screen.findByText(/Gathering questions from reviews, issues and the paper/)).toBeInTheDocument();
  expect(screen.getByLabelText("Ask about this paper", { selector: "input" })).toBeInTheDocument();
});

test("empty and error states say what to do next", async () => {
  mockFetch((url) => (faqUrl(url) ? json({ items: [] }) : undefined));
  const { unmount } = open();
  expect(await screen.findByText("No questions yet. Ask one above.")).toBeInTheDocument();
  unmount();

  let fail = true;
  mockFetch((url) => {
    if (!faqUrl(url)) return undefined;
    return fail ? json({ error: { code: "x", message: "boom" } }, 500) : json({ items: ITEMS });
  });
  open();
  expect(await screen.findByText(/Couldn't load the questions/)).toBeInTheDocument();
  fail = false;
  click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Why scaling?")).toBeInTheDocument();
});

test("filters show only the kinds present, with counts, and narrow the list", async () => {
  mockFetch((url) => (faqUrl(url) ? json({ items: ITEMS }) : undefined));
  open();
  await screen.findByText("Why scaling?");
  const filters = screen.getByRole("group", { name: "Filter by source" });
  expect(within(filters).getByRole("button", { name: "All 5" })).toHaveAttribute("aria-pressed", "true");
  for (const name of [
    "Reviewer discussion 1",
    "GitHub issues 1",
    "Findings 1",
    "Anticipated 1",
    "Your questions 1",
  ]) {
    expect(within(filters).getByRole("button", { name })).toBeInTheDocument();
  }
  expect(within(filters).queryByRole("button", { name: /Paper page/ })).toBeNull();

  click(within(filters).getByRole("button", { name: "GitHub issues 1" }));
  expect(screen.getByText("Why mask?")).toBeInTheDocument();
  expect(screen.queryByText("Why scaling?")).toBeNull();
  click(within(filters).getByRole("button", { name: "All 5" }));
  expect(screen.getByText("Why scaling?")).toBeInTheDocument();
});

test("a supported answer shows its source link and citations; an unsupported one says so", async () => {
  mockFetch((url) => (faqUrl(url) ? json({ items: ITEMS }) : undefined));
  open();
  const scaling = (await screen.findByText("Why scaling?")).closest("article") as HTMLElement;
  const link = within(scaling).getByRole("link", { name: "From reviewer discussion" });
  expect(link).toHaveAttribute("href", "https://openreview.net/forum?id=abc");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(within(scaling).getByText("Eq. 1")).toBeInTheDocument();
  expect(within(scaling).getByText("§3.2")).toBeInTheDocument();
  expect(within(scaling).getByText("Because of scaling.")).toBeInTheDocument();

  const nope = screen.getByText("Why nope?").closest("article") as HTMLElement;
  expect(within(nope).getByText(/Not supported by the sources/)).toBeInTheDocument();
  expect(within(nope).queryByText("Because of nope.")).toBeNull();
  expect(within(nope).queryByRole("button", { name: /Show on Chart/ })).toBeNull();
  expect(within(nope).queryByRole("link")).toBeNull();
});

test("a source URL that isn't a web link is shown as plain text", async () => {
  const bad = item("bad", "github_issue", { source_url: "javascript:alert(1)", source_label: "From a GitHub issue" });
  mockFetch((url) => (faqUrl(url) ? json({ items: [bad] }) : undefined));
  open();
  const el = (await screen.findByText("Why bad?")).closest("article") as HTMLElement;
  expect(within(el).getByText("From a GitHub issue")).toBeInTheDocument();
  expect(within(el).queryByRole("link")).toBeNull();
});

test("answer text is escaped, never run", async () => {
  const evil = item("evil", "anticipated", { answer_markdown: "<script>window.__pwned = 1</script> **bold**" });
  mockFetch((url) => (faqUrl(url) ? json({ items: [evil] }) : undefined));
  const { container } = open();
  await screen.findByText("Why evil?");
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<script>window.__pwned = 1</script>");
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
});

test("Show on Chart and Find on Route hand the concept to the store, named from cached layers", async () => {
  mockFetch((url) => (faqUrl(url) ? json({ items: ITEMS }) : undefined));
  open();
  const scaling = (await screen.findByText("Why scaling?")).closest("article") as HTMLElement;
  expect(within(scaling).getByText("Self-attention")).toBeInTheDocument();

  click(within(scaling).getByRole("button", { name: /Show on Chart/ }));
  expect(useUI.getState().view).toBe("chart");
  expect(useUI.getState().pendingFocus).toEqual({ conceptId: "self_attention", view: "chart" });

  click(within(scaling).getByRole("button", { name: /Find on Route/ }));
  expect(useUI.getState().view).toBe("route");
  expect(useUI.getState().pendingFocus).toEqual({ conceptId: "self_attention", view: "route" });
});

test("a concept below the master layer is named by asking the server for it", async () => {
  const deep = item("deep", "reviewer_discussion", { concept_ids: ["sqrt-dk-scaling"] });
  mockFetch((url) => {
    if (faqUrl(url)) return json({ items: [deep] });
    if (url.endsWith("/concepts/sqrt-dk-scaling")) {
      return json({
        concept: {
          id: "sqrt-dk-scaling",
          canonical_name: "scaling",
          title: "Scaling by √d_k",
          gist: "g",
          grounding: "in_code",
          is_first_principle: true,
          depth: 3,
          anchors: [],
          has_layer: false,
          gen_state: "detailed",
          issue_count_self: 0,
        },
        also_appears_in: [],
        issues_inside: 0,
        details: { explanation: false, code: false, example: false, graph: false },
      });
    }
    return undefined;
  });
  open();
  const el = (await screen.findByText("Why deep?")).closest("article") as HTMLElement;
  expect(await within(el).findByRole("button", { name: "Show on Chart for Scaling by √d_k" })).toBeInTheDocument();
  click(within(el).getByRole("button", { name: /Show on Chart/ }));
  expect(useUI.getState().pendingFocus).toEqual({ conceptId: "sqrt-dk-scaling", view: "chart" });
});

test("asking: the answer appears first, marked in words, and the box clears", async () => {
  const answer = item("asked1", "user_asked", {
    question: "Is the mask applied before softmax?",
    answer_markdown: "Yes, masked scores are set to a large negative number first.",
    source_label: "You asked",
    anchors_cited: ["sec:3.2"],
  });
  const calls = mockFetch((url) => {
    if (faqUrl(url)) return json({ items: ITEMS });
    if (askUrl(url)) return json(answer);
    return undefined;
  });
  open();
  await screen.findByText("Why scaling?");
  const box = screen.getByRole("textbox", { name: "Ask about this paper" });
  ask(box, "Is the mask applied before softmax?");

  expect(await screen.findByText("Yes, masked scores are set to a large negative number first.")).toBeInTheDocument();
  expect(calls.some(askUrl)).toBe(true);
  const articles = screen.getAllByRole("article");
  expect(within(articles[0] as HTMLElement).getByText("Just asked")).toBeInTheDocument();
  expect(within(articles[0] as HTMLElement).getByText("Is the mask applied before softmax?")).toBeInTheDocument();
  expect(box).toHaveValue("");
  expect(screen.getByRole("button", { name: "Your questions 2" })).toBeInTheDocument();
});

test("asking: while waiting the box is disabled and says what is happening", async () => {
  let release: (r: Response) => void = () => undefined;
  mockFetch((url) => {
    if (faqUrl(url)) return json({ items: ITEMS });
    if (askUrl(url)) return new Promise<Response>((r) => (release = r)) as unknown as Response;
    return undefined;
  });
  open();
  await screen.findByText("Why scaling?");
  ask(screen.getByRole("textbox", { name: "Ask about this paper" }), "How?");
  expect(await screen.findByText("Reading the paper and code…")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Ask about this paper" })).toBeDisabled();
  await act(async () => {
    release(json(item("late", "user_asked", { question: "How?" })));
  });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Ask about this paper" })).toBeEnabled());
});

test("asking: a failure shows the plain message and keeps the question", async () => {
  mockFetch((url) => {
    if (faqUrl(url)) return json({ items: ITEMS });
    if (askUrl(url)) return json({ error: { code: "answer_failed", message: "Couldn't write an answer. Try again." } }, 502);
    return undefined;
  });
  open();
  await screen.findByText("Why scaling?");
  const box = screen.getByRole("textbox", { name: "Ask about this paper" });
  ask(box, "What is d_k?");
  expect(await screen.findByText("Couldn't write an answer. Try again.")).toBeInTheDocument();
  expect(box).toHaveValue("What is d_k?");
  expect(box).toBeEnabled();
});

test("an empty question is not sent", async () => {
  const calls = mockFetch((url) => (faqUrl(url) ? json({ items: ITEMS }) : undefined));
  open();
  await screen.findByText("Why scaling?");
  expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  fireEvent.submit(screen.getByRole("search", { name: "Ask about this paper" }));
  expect(calls.some(askUrl)).toBe(false);
});

test("'/' focuses the ask box unless the user is already typing", async () => {
  mockFetch((url) => (faqUrl(url) ? json({ items: ITEMS }) : undefined));
  open();
  await screen.findByText("Why scaling?");
  const box = screen.getByRole("textbox", { name: "Ask about this paper" });
  expect(box).not.toHaveFocus();

  const notPrevented = fireEvent.keyDown(document.body, { key: "/" });
  expect(notPrevented).toBe(false); // the page claimed the key
  expect(box).toHaveFocus();

  // Already typing: the key is left alone so "/" can be part of the question.
  expect(fireEvent.keyDown(box, { key: "/" })).toBe(true);
  expect(box).toHaveFocus();
});
