// M6 detail tabs against the real backend (mock provider) and the seeded demo paper:
// Code with arrow callouts, Example with "Another example", and Graph.
// Seed: backend/portolan/dev/seed.py. Everything below is served from the database; no LLM runs.

import { expect, test, type Locator, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const node = (page: Page, title: string) =>
  page.locator(".react-flow__node").and(page.getByRole("group", { name: new RegExp(`^${escapeRe(title)},`) }));
const drawer = (page: Page) => page.getByRole("complementary", { name: "Concept details" });
const tab = (page: Page, name: string) => drawer(page).getByRole("tab", { name });

async function settled(page: Page, title: string) {
  await expect
    .poll(
      () =>
        page.evaluate(async (label) => {
          const el = document.querySelector(`.react-flow__node[aria-label^="${label},"]`);
          const vp = document.querySelector(".react-flow__viewport");
          if (!el || !vp) return false;
          let o = 1;
          for (let e: Element | null = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity);
          const before = getComputedStyle(vp).transform;
          await new Promise((r) => setTimeout(r, 120));
          return o === 1 && getComputedStyle(vp).transform === before;
        }, title),
      { message: `layer with "${title}" didn't settle` },
    )
    .toBe(true);
}

async function open(page: Page, title: string, jumpInto?: string) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(node(page, "Self-attention")).toBeVisible();
  if (jumpInto) {
    await node(page, jumpInto).dblclick();
    await settled(page, title);
  }
  await node(page, title).click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: title })).toBeVisible();
  // The default explanation shows first; wait for it so later tabs aren't racing the first request.
  await expect(drawer(page)).toContainText("Why it's here");
}

/** For each callout, the line number of the code row it sits under. */
const calloutLines = (region: Locator) =>
  region.getByRole("note").evaluateAll((notes) =>
    notes.map((n) => {
      let el = n.previousElementSibling;
      while (el && !el.hasAttribute("data-line")) el = el.previousElementSibling;
      return Number(el?.getAttribute("data-line"));
    }),
  );

test("Code tab: file link, highlighted lines, callouts under their lines, mismatch glyph", async ({ page }) => {
  await open(page, "Layer normalization");
  const explainWidth = (await drawer(page).boundingBox())?.width ?? 0;

  await tab(page, "Code").click();
  const region = drawer(page).getByRole("region", { name: /toyformer\/layers\.py, lines 20–28/ });
  await expect(region).toBeVisible();
  expect((await drawer(page).boundingBox())?.width ?? 0).toBeGreaterThan(explainWidth + 80); // widens (~520 px)

  const link = drawer(page).getByRole("link", { name: "toyformer/layers.py" });
  await expect(link).toHaveAttribute("href", /github\.com\/ada-example\/toyformer\/blob\/5e1f0c7[0-9a-f]*\/toyformer\/layers\.py#L20-L28/);
  await expect(drawer(page).getByText("Lines 20-28 wrap each sublayer")).toBeVisible();
  await expect(drawer(page).getByText("torch.nn.LayerNorm")).toBeVisible();

  // Three notes; two on line 23, one on line 28, each directly below its line.
  await expect(region.getByRole("note")).toHaveCount(3);
  expect(await calloutLines(region)).toEqual([23, 23, 28]);
  const row28 = await region.locator('[data-line="28"]').boundingBox();
  const mismatch = region.locator('[role=note][data-kind=mismatch]');
  const note28 = await mismatch.boundingBox();
  expect(note28 && row28 && note28.y >= row28.y + row28.height - 1).toBe(true);

  // Annotated lines are highlighted; the others recede.
  await expect(region.locator('[data-line="23"]')).toHaveClass(/rowNoted/);
  await expect(region.locator('[data-line="28"]')).toHaveClass(/rowMismatch/);
  await expect(region.locator('[data-line="21"]')).not.toHaveClass(/rowNoted|rowMismatch|rowContext/);

  // The mismatch is marked with a "!" glyph and plain words, not just a colour.
  await expect(mismatch.getByText("!", { exact: true })).toBeVisible();
  await expect(mismatch).toContainText("Mismatch:");
  await expect(mismatch).toContainText("pre-norm");
  const other = region.locator("[role=note][data-kind=detail_not_in_paper]");
  await expect(other).toContainText("Not in the paper:");
  await expect(other.getByText("!", { exact: true })).toHaveCount(0);
});

test("Code tab: every note kind is told apart, and a library call is named", async ({ page }) => {
  await open(page, "Scaled dot-product", "Self-attention");
  await tab(page, "Code").click();
  const region = drawer(page).getByRole("region", { name: /toyformer\/attention\.py, lines 11–18/ });
  await expect(region.getByRole("note")).toHaveCount(4);
  expect(await calloutLines(region)).toEqual([13, 14, 16, 17]);
  for (const kind of ["implements", "detail_not_in_paper", "context"]) {
    await expect(region.locator(`[role=note][data-kind=${kind}]`)).not.toHaveCount(0);
  }
  await expect(region.locator("[role=note][data-kind=mismatch]")).toHaveCount(0);
  await expect(drawer(page).getByText("torch.nn.functional.softmax")).toBeVisible();
});

test("Code tab: a concept the code never spells out says so and leads to the Example tab", async ({ page }) => {
  await open(page, "Label smoothing");
  await tab(page, "Code").click();
  await expect(drawer(page)).toContainText("isn't spelled out in the code");
  await expect(drawer(page).getByRole("note")).toHaveCount(0);
  await drawer(page).getByRole("button", { name: "See an example" }).click();
  await expect(tab(page, "Example")).toHaveAttribute("aria-selected", "true");
  await expect(drawer(page).getByText("Target for class 2 of 5")).toBeVisible();
  await expect(drawer(page).getByRole("cell", { name: "0.92" })).toBeVisible();
});

test("Example tab: a table of computed numbers and Another example with a way back", async ({ page }) => {
  await open(page, "Softmax", "Self-attention");
  await tab(page, "Example").click();
  await expect(drawer(page).getByText("softmax([2.0, 1.0, 0.1])")).toBeVisible();
  await expect(drawer(page).getByText("Computed by running a snippet")).toBeVisible();
  const cell = drawer(page).getByRole("cell", { name: "0.659" });
  await expect(cell).toBeVisible();
  expect(await cell.evaluate((el) => getComputedStyle(el).textAlign)).toBe("right");

  await drawer(page).getByRole("button", { name: "Another example" }).click();
  await expect(drawer(page).getByText(/why libraries subtract the max/)).toBeVisible();
  await expect(drawer(page).getByText("Example 2 of 2")).toBeVisible();
  await expect(drawer(page).getByRole("cell", { name: "nan" }).first()).toBeVisible();

  await drawer(page).getByRole("button", { name: "Previous" }).click();
  await expect(drawer(page).getByText("softmax([2.0, 1.0, 0.1])")).toBeVisible();
  await expect(drawer(page).getByText("Example 1 of 2")).toBeVisible();
});

test("Graph tab appears only for concepts that have a graph, and draws an SVG", async ({ page }) => {
  await open(page, "Softmax", "Self-attention");
  await expect(tab(page, "Graph")).toBeVisible();
  await tab(page, "Graph").click();
  const chart = drawer(page).getByRole("img", { name: /^Bar chart\. softmax/ });
  await expect(chart).toBeVisible();
  expect(await chart.evaluate((el) => el.tagName.toLowerCase())).toBe("svg");
  await expect(drawer(page).getByRole("list", { name: "Legend" })).toContainText("scoreweight");
  await expect(drawer(page).getByRole("table")).toHaveCount(1); // the same numbers, for screen readers
  await expect(drawer(page)).toContainText("shares add up to 1");

  // A concept with no graph never gets the tab (and no request waits on it).
  await page.keyboard.press("Escape");
  await node(page, "Attention weights").click();
  await expect(tab(page, "Graph")).toBeVisible(); // this one is a heatmap
  await node(page, "Queries, keys and values").click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Queries, keys and values" })).toBeVisible();
  await expect(drawer(page)).toContainText("Why it's here");
  await expect(tab(page, "Graph")).toHaveCount(0);
});
