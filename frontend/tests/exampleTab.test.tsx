import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import type { Example } from "../src/api/types";
import { MAX_EXAMPLES, numericColumns } from "../src/detail/ExampleTab";
import {
  CONCEPT,
  CONCEPT_PAYLOAD,
  json,
  mockFetch,
  paperUrl,
  PAPER,
  renderDrawer,
} from "./detailHarness";

const example = (variant: number, extra: Partial<Example> = {}): Example => ({
  concept_id: CONCEPT,
  variant,
  source_label: "paper_notation",
  caption: variant === 0 ? "softmax([2.0, 1.0, 0.1])" : "softmax([1000, 1001])",
  program: "print('x')",
  columns: ["score", "weight", "note"],
  rows: [
    ["2.0", "0.659", "largest"],
    ["1.0", "0.242", "middle"],
  ],
  note: variant === 0 ? "The weights sum to 1." : "Large scores overflow without the max trick.",
  ...extra,
});

function route(url: string) {
  if (paperUrl(url)) return json({ paper_id: PAPER, status: "ready", repo: null });
  const m = /\/example\?variant=(\d+)/.exec(url);
  if (m) return json(example(Number(m[1])));
  if (url.includes(`/concepts/${CONCEPT}`) && !url.includes("/explanation")) return json(CONCEPT_PAYLOAD());
  return undefined;
}

async function openExample() {
  renderDrawer();
  fireEvent.click(screen.getByRole("tab", { name: "Example" }));
  await screen.findByText("softmax([2.0, 1.0, 0.1])");
}

test("numeric columns are the ones where every cell is a number", () => {
  expect(numericColumns(["a", "b", "c"], [["1", "x", "-2.5e3"], ["2,000", "y", "7%"]])).toEqual([true, false, true]);
  expect(numericColumns(["a"], [])).toEqual([false]);
});

test("shows the source, caption, a table with numbers right-aligned in mono, and the note", async () => {
  mockFetch(route);
  await openExample();
  expect(screen.getByText("Computed by running a snippet")).toBeInTheDocument();
  expect(screen.getByText("The weights sum to 1.")).toBeInTheDocument();
  const cell = screen.getByRole("cell", { name: "0.659" });
  expect(cell).toHaveClass("numCell", "mono");
  expect(screen.getByRole("cell", { name: "largest" })).not.toHaveClass("numCell");
  expect(screen.getByRole("columnheader", { name: "weight" })).toHaveClass("numCell");
});

test("a run of the repo is labelled as such", async () => {
  mockFetch((url) => (/variant=0/.test(url) ? json(example(0, { source_label: "from_repo_run" })) : route(url)));
  await openExample();
  expect(screen.getByText("From running the repo")).toBeInTheDocument();
});

test("the snippet sits behind a disclosure", async () => {
  mockFetch(route);
  await openExample();
  const summary = screen.getByText("Show the snippet");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(summary.closest("details")).toHaveAttribute("open");
  expect(screen.getByText("print('x')")).toBeInTheDocument();
});

test("Another example asks for the next variant, and earlier ones are one click back without a new request", async () => {
  const calls = mockFetch(route);
  await openExample();
  expect(screen.queryByText(/Example \d of/)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Another example" }));
  expect(await screen.findByText("softmax([1000, 1001])")).toBeInTheDocument();
  expect(screen.getByText("Example 2 of 2")).toBeInTheDocument();
  expect(calls.filter((u) => u.includes("variant=1"))).toHaveLength(1);
  // Looking at the newest one, so Another is allowed; Next is not.
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(await screen.findByText("softmax([2.0, 1.0, 0.1])")).toBeInTheDocument();
  expect(screen.getByText("Example 1 of 2")).toBeInTheDocument();
  expect(calls.filter((u) => u.includes("variant=0"))).toHaveLength(1); // cached
  // Not on the newest: Another is held back so there is exactly one way forward.
  expect(screen.getByRole("button", { name: "Another example" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("softmax([1000, 1001])")).toBeInTheDocument();
});

test(`stops asking after ${MAX_EXAMPLES} examples`, async () => {
  mockFetch(route);
  await openExample();
  for (let i = 1; i < MAX_EXAMPLES; i++) {
    fireEvent.click(screen.getByRole("button", { name: "Another example" }));
    await screen.findByText(`Example ${i + 1} of ${i + 1}`);
  }
  expect(screen.getByRole("button", { name: "Another example" })).toBeDisabled();
});

test("while the example is being computed, reserved skeleton lines show instead of numbers", async () => {
  mockFetch((url) => (url.includes("/example") ? json({ status: "generating" }, 202) : route(url)));
  renderDrawer();
  fireEvent.click(screen.getByRole("tab", { name: "Example" }));
  await waitFor(() => expect(document.querySelector("[aria-busy=true]")).not.toBeNull());
  expect(screen.queryByRole("table")).toBeNull();
});

test("a failure shows the sentence from the backend", async () => {
  mockFetch((url) =>
    url.includes("/example")
      ? json({ error: { code: "generation_failed", message: "Couldn't write this part. Try again." } }, 502)
      : route(url),
  );
  renderDrawer();
  fireEvent.click(screen.getByRole("tab", { name: "Example" }));
  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText("Couldn't write this part. Try again.")).toBeInTheDocument();
});
