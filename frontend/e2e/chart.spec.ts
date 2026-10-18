// M5 Chart flow against the real backend (mock provider) and the seeded demo paper.
// Seed: backend/portolan/dev/seed.py — Toyformer › Self-attention › Scaled dot-product.

import { expect, test, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** A Chart node: React Flow renders it as role=group, labelled "Title, grounding, …". */
const node = (page: Page, title: string) =>
  page.locator(".react-flow__node").and(page.getByRole("group", { name: new RegExp(`^${escapeRe(title)},`) }));
const drawer = (page: Page) => page.getByRole("complementary", { name: "Concept details" });
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Where you are on the map" });

/** Wait for a jump to finish: node fully opaque and the viewport no longer moving. */
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

async function openDemo(page: Page) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(page.getByRole("heading", { level: 1, name: /Toyformer/ })).toBeVisible();
  await expect(node(page, "Self-attention")).toBeVisible();
}

test("map → drawer → jump in → back → edge popover", async ({ page }) => {
  await openDemo(page);

  // Master layer: the center and its eight concepts, every grounding shape on screen.
  await expect(page.locator(".react-flow__node")).toHaveCount(9);
  for (const title of ["Toyformer", "Positional encoding", "Layer normalization", "Multi-head attention"]) {
    await expect(node(page, title)).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "Legend" })).toBeVisible();

  // Single click selects and opens the drawer with the default explanation.
  await node(page, "Self-attention").click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Self-attention" })).toBeVisible();
  await expect(drawer(page)).toContainText("each token builds its new representation");
  await expect(drawer(page)).toContainText("Eq. 1");

  // Double-click jumps in; the breadcrumbs follow.
  await node(page, "Self-attention").dblclick();
  await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
  await settled(page, "Queries, keys and values");
  await expect(node(page, "Layer normalization")).toHaveCount(0);

  // One more level: Toyformer › Self-attention › Scaled dot-product.
  await node(page, "Scaled dot-product").dblclick();
  await expect(crumbs(page)).toContainText(/Toyformer\s*›\s*Self-attention\s*›\s*Scaled dot-product/);
  await settled(page, "Dot product");

  // Esc closes the drawer first, then goes back one level.
  await node(page, "Dot product").click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Dot product" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer(page)).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
  // The concept we came out of is selected (spec §13.4).
  await expect(node(page, "Scaled dot-product").locator("[data-state*=selected]")).toBeVisible();

  // Breadcrumb click goes straight to the top.
  await crumbs(page).getByRole("button", { name: "Toyformer" }).click();
  await settled(page, "Layer normalization");

  // Double-click an edge label → popover with the edge explanation.
  await page.getByText("needs", { exact: true }).dblclick();
  const popover = page.getByRole("dialog", { name: "Why these are linked" });
  await expect(popover).toContainText("Self-attention needs positional encoding");
  await expect(popover).toContainText("Why it matters");
  await page.keyboard.press("Escape");
  await expect(popover).toHaveCount(0);
  // Esc with nothing open at the top level stays on the master map.
  await expect(node(page, "Toyformer")).toBeVisible();
});

test("Fit view and Back buttons work without the keyboard", async ({ page }) => {
  await openDemo(page);
  await node(page, "Positional encoding").dblclick();
  await settled(page, "Sinusoids of different frequencies");
  await page.getByRole("button", { name: "Fit view" }).click();
  await crumbs(page).getByRole("button", { name: "Back one level" }).click();
  await expect(node(page, "Self-attention")).toBeVisible();
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("jumping in swaps layers without animating", async ({ page }) => {
    await openDemo(page);
    // Hover > 300 ms prefetches the child layer, so this measures the swap, not the network.
    await node(page, "Self-attention").hover();
    await page.waitForTimeout(600);

    // Time from the double-click to the child layer being fully opaque, measured in the page.
    await page.evaluate((label) => {
      const w = window as unknown as { __jump: Promise<number> };
      w.__jump = new Promise<number>((resolve) => {
        let start = 0;
        document.addEventListener("dblclick", () => (start = performance.now()), { capture: true, once: true });
        const opacity = (el: Element | null): number => {
          let o = 1;
          for (let e = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity);
          return o;
        };
        const tick = () => {
          const el = document.querySelector(`.react-flow__node[aria-label^="${label},"]`);
          if (start && el && opacity(el) === 1) resolve(performance.now() - start);
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    }, "Queries, keys and values");
    await node(page, "Self-attention").dblclick();
    const ms = await page.evaluate(() => (window as unknown as { __jump: Promise<number> }).__jump);
    test.info().annotations.push({ type: "jump-in (reduced motion)", description: `${Math.round(ms)} ms` });
    console.log(`[perf] jump-in with reduced motion: ${Math.round(ms)} ms`);
    expect(ms).toBeLessThan(350); // the animated path takes ~600 ms (spec §13.4)

    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running" && a.effect?.getTiming().duration !== 0).length,
    );
    expect(running).toBe(0);
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
  });
});
