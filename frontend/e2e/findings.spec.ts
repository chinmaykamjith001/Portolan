// M8 findings UI against the real backend (mock provider) and the seeded demo paper:
// the paper-strip count, the Findings panel, "Show on Chart", the drawer verdict and the
// Code tab's verdict box. Seed: backend/portolan/dev/seed_findings.py (no LLM runs).

import { expect, test, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const node = (page: Page, title: string) =>
  page.locator(".react-flow__node").and(page.getByRole("group", { name: new RegExp(`^${escapeRe(title)},`) }));
const drawer = (page: Page) => page.getByRole("complementary", { name: "Concept details" });
const panel = (page: Page) => page.getByRole("dialog", { name: "Findings" });

async function openPaper(page: Page) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(node(page, "Self-attention")).toBeVisible();
}

test("the paper strip counts mismatches and opens the Findings panel", async ({ page }) => {
  await openPaper(page);
  const control = page.getByRole("button", { name: /^Findings: \d+ mismatch(es)?/ });
  await expect(control).toBeVisible();
  await control.click();
  await expect(panel(page)).toBeVisible();

  // Mismatches first, each with the claim, the evidence and a shape cue; quiet results are folded.
  const rows = panel(page).getByRole("listitem");
  await expect(rows.first()).toHaveAttribute("data-verdict", "mismatch");
  await expect(rows.first()).toContainText(/paper|code/i);
  await expect(panel(page).getByRole("button", { name: /^Show \d+ lower-confidence and matching/ })).toBeVisible();
  const before = await rows.count();
  await panel(page).getByRole("button", { name: /^Show \d+ lower-confidence and matching/ }).click();
  expect(await rows.count()).toBeGreaterThan(before);

  await page.keyboard.press("Escape");
  await expect(panel(page)).toBeHidden();
});

test("Show on Chart reveals the concept; the drawer and Code tab carry the verdict", async ({ page }) => {
  await openPaper(page);
  // The planted mismatch is on Layer normalization: its node carries the outline state and "!".
  const ln = node(page, "Layer normalization");
  await expect(ln).toBeVisible();
  await expect(ln.locator("[data-state~='mismatch']")).toBeVisible();
  await expect(ln).toContainText("!");

  await page.getByRole("button", { name: /^Findings:/ }).click();
  const row = panel(page).getByRole("listitem").filter({ hasText: /layer-normalized/i }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Show on Chart" }).click();
  await expect(panel(page)).toBeHidden();
  await expect(node(page, "Layer normalization")).toBeVisible();

  // "Show on Chart" selects the concept, so its drawer opens with the verdict in the header.
  const d = drawer(page);
  await expect(d.getByRole("heading", { level: 2, name: "Layer normalization" })).toBeVisible();
  await expect(d.getByText("! Mismatch")).toBeVisible();

  await d.getByRole("tab", { name: "Code" }).click();
  const box = d.getByRole("note").filter({ hasText: "The code differs from the paper here." });
  await expect(box).toBeVisible();
  await expect(box).toContainText(/paper|code/i); // the evidence behind the verdict
});

test("a concept whose sub-map holds findings shows an issues-inside count", async ({ page }) => {
  // The demo's only mismatch is a direct child of the master map, so no seeded concept has
  // `issues_inside > 0` there. Give one the count the backend would send for a deeper mismatch.
  await page.route("**/api/papers/*/chart/master", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.concepts["self-attention"].issues_inside = 2;
    await route.fulfill({ response: res, json: body });
  });
  await openPaper(page);
  const badge = node(page, "Self-attention").getByLabel("2 issues inside");
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText("2");
  await expect(node(page, "Feed-forward").getByLabel(/issues inside/)).toHaveCount(0);
});
