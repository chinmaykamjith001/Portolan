// M7 Route flow against the real backend (mock provider) and the seeded demo paper.
// Seed: backend/portolan/dev/seed_route.py — nine stops, sub-routes under stops 2 and 3, a recurring
// concept (stops 3 and 6), an appendix side road on stop 8.

import { expect, test, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** A Route stop: React Flow renders it as role=group, labelled "Stop N, §x, Title, …". */
const stop = (page: Page, n: number) =>
  page.locator(".react-flow__node").and(page.getByRole("group", { name: new RegExp(`^Stop ${n},`) }));
const stopTitled = (page: Page, n: number, section: string, title: string) =>
  page
    .locator(".react-flow__node")
    .and(page.getByRole("group", { name: new RegExp(`^Stop ${n}, ${escapeRe(section)}, ${escapeRe(title)}`) }));
const current = (_page: Page, locator: ReturnType<typeof stop>) => locator.locator("[data-state~=current]");
const drawer = (page: Page) => page.getByRole("complementary", { name: "Concept details" });
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Where you are on the route" });
const chartCrumbs = (page: Page) => page.getByRole("navigation", { name: "Where you are on the map" });
const tab = (page: Page, name: string) => page.getByRole("tab", { name });
const chartNode = (page: Page, title: string) =>
  page
    .locator(".react-flow__node")
    .and(page.getByRole("group", { name: new RegExp(`^${escapeRe(title)},`) }));

/** Wait for a jump to finish: the stop fully opaque and the viewport no longer moving. */
async function settled(page: Page, labelStart: string) {
  await expect
    .poll(
      () =>
        page.evaluate(async (label) => {
          const el = document.querySelector(`.react-flow__node[aria-label^="${label}"]`);
          const vp = document.querySelector(".react-flow__viewport");
          if (!el || !vp) return false;
          let o = 1;
          for (let e: Element | null = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity);
          const before = getComputedStyle(vp).transform;
          await new Promise((r) => setTimeout(r, 120));
          return o === 1 && getComputedStyle(vp).transform === before;
        }, labelStart),
      { message: `route with "${labelStart}" didn't settle` },
    )
    .toBe(true);
}

async function openRoute(page: Page) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(page.getByRole("heading", { level: 1, name: /Toyformer/ })).toBeVisible();
  await tab(page, "Route").click();
  await expect(tab(page, "Route")).toHaveAttribute("aria-selected", "true");
  await expect(stop(page, 1)).toBeVisible();
  // Let the road settle (viewport placed, stops' concepts loaded) before interacting with it.
  await expect(stop(page, 9).getByRole("img", { name: "Shown by running" })).toBeAttached();
  await settled(page, "Stop 1,");
}

async function center(_page: Page, locator: ReturnType<typeof stop>) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("stop has no box");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

test("the road: stops in order along an S, with sections, transitions and the paper's own words", async ({ page }) => {
  await openRoute(page);

  // Nine stops, in the order of the paper.
  await expect(page.locator(".react-flow__node").and(page.getByRole("group", { name: /^Stop \d+,/ }))).toHaveCount(9);
  await expect(stopTitled(page, 1, "§1", "A transformer small enough to read")).toBeVisible();
  await expect(stopTitled(page, 4, "Eq. 1", "Scores, scaled and normalized")).toBeVisible();
  await expect(stopTitled(page, 9, "Eq. 4", "Warmup, then decay")).toBeVisible();

  // The S: center → right → left → center …, and an even step down.
  const p1 = await center(page, stop(page, 1));
  const p2 = await center(page, stop(page, 2));
  const p3 = await center(page, stop(page, 3));
  const p4 = await center(page, stop(page, 4));
  const p5 = await center(page, stop(page, 5));
  const p6 = await center(page, stop(page, 6));
  const p7 = await center(page, stop(page, 7));
  expect(p2.x).toBeGreaterThan(p1.x + 100);
  expect(p3.x).toBeLessThan(p1.x - 100);
  expect(Math.abs(p4.x - p1.x)).toBeLessThan(2);
  expect(p5.x).toBeGreaterThan(p4.x + 100);
  expect(p6.x).toBeLessThan(p4.x - 100);
  expect(Math.abs(p7.x - p1.x)).toBeLessThan(2);
  const steps = [p2.y - p1.y, p3.y - p2.y, p4.y - p3.y, p5.y - p4.y, p6.y - p5.y, p7.y - p6.y];
  for (const s of steps) expect(Math.abs(s - (steps[0] ?? 0))).toBeLessThan(2);

  // Segment markers beside the first stop of each section; transitions along the connectors.
  for (const t of ["§1 Introduction", "§2 Model", "§3 Training"]) await expect(page.getByText(t, { exact: true })).toBeVisible();
  await expect(page.getByText("so the model is laid out in three parts,")).toBeVisible();

  // Appendix side road on stop 8; recurring concept on stops 3 and 6.
  await expect(stop(page, 8).getByLabel("Side road: Appendix A")).toBeAttached();
  await expect(stop(page, 3).getByText("Seen again at stop 6")).toBeAttached();
  await expect(stop(page, 6).getByText("First mentioned at stop 3")).toBeAttached();
});

test("Next, Previous and the arrow keys walk the road; a stop shows what the paper says there", async ({ page }) => {
  await openRoute(page);
  await expect(page.getByText(/^Stop \d+ of 9$/)).toHaveCount(0); // nothing is current yet

  await page.getByRole("button", { name: "Next stop" }).click();
  await expect(current(page, stop(page, 1))).toBeVisible();
  await expect(page.getByText("Stop 1 of 9")).toBeVisible();

  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(current(page, stop(page, 3))).toBeVisible();
  await expect(page.getByText("Stop 3 of 9")).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(current(page, stop(page, 2))).toBeVisible();
  await page.getByRole("button", { name: "Previous stop" }).click();
  await expect(current(page, stop(page, 1))).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous stop" })).toBeDisabled();

  // Travel is drawn in the accent: after stop 1, no connector has been traveled; at stop 3, two have.
  await expect(page.locator("[data-traveled]")).toHaveCount(0);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("[data-traveled]")).toHaveCount(2);

  // Stepping to a far stop pans it into view.
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowRight");
  await expect(page.getByText("Stop 9 of 9")).toBeVisible();
  await expect
    .poll(async () => {
      const box = await stop(page, 9).boundingBox();
      const vp = page.viewportSize();
      return !!box && !!vp && box.y >= 0 && box.y + box.height <= vp.height;
    })
    .toBe(true);

  // Walk back to stop 3 (it is off screen now) and click it: the drawer says what the paper says
  // here, above the concept's explanation.
  for (let i = 0; i < 6; i++) await page.keyboard.press("ArrowLeft");
  await expect(page.getByText("Stop 3 of 9")).toBeVisible();
  await stop(page, 3).click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Self-attention" })).toBeVisible();
  const says = drawer(page).getByRole("region", { name: "What the paper says here" });
  await expect(says).toContainText("weighted mix of every token");
  await expect(says).toContainText("§2.1");
  await expect(drawer(page)).toContainText("Route · stop 3 · §2.1");
  await expect(drawer(page)).toContainText("each token builds its new representation"); // the Explain tab
  expect(
    await drawer(page).evaluate((el) => {
      const a = el.querySelector("[aria-label='What the paper says here']");
      const t = el.querySelector("[role=tablist]");
      return !!a && !!t && !!(a.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING);
    }),
  ).toBe(true);

  // The recurring concept: the links move the current stop and keep the drawer on that stop.
  await says.getByRole("button", { name: /^stop 6/ }).click();
  await expect(current(page, stop(page, 6))).toBeVisible();
  await expect(drawer(page).getByRole("region", { name: "What the paper says here" })).toContainText("First mentioned at");
  await drawer(page).getByRole("region", { name: "What the paper says here" }).getByRole("button", { name: /^stop 3/ }).click();
  await expect(current(page, stop(page, 3))).toBeVisible();

  // Esc closes the drawer; the current stop stays.
  await page.keyboard.press("Escape");
  await expect(drawer(page)).toHaveCount(0);
  await expect(current(page, stop(page, 3))).toBeVisible();
});

test("jump into a stop's sub-route and back, staying on the Route", async ({ page }) => {
  await openRoute(page);

  // Double-click stop 2: its sub-route grows in the same S; the Route tab stays selected.
  await stop(page, 2).dblclick();
  await expect(crumbs(page)).toContainText(/Route\s*›\s*Three working parts/);
  await settled(page, "Stop 1, §2.1, Attention");
  await expect(stopTitled(page, 2, "§2.2", "Positional encoding")).toBeVisible();
  await expect(page.getByRole("group", { name: /Three working parts/ })).toHaveCount(0);
  await expect(tab(page, "Route")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".react-flow__node").and(page.getByRole("group", { name: /^Stop \d+,/ }))).toHaveCount(2);

  // Esc goes back with the stop we came out of current.
  await page.keyboard.press("Escape");
  await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Route");
  await settled(page, "Stop 9,");
  await expect(current(page, stop(page, 2))).toBeVisible();
  await expect(tab(page, "Route")).toHaveAttribute("aria-selected", "true");

  // Enter jumps into the current stop; the "Jump in" button works too; a breadcrumb returns.
  await stop(page, 3).click();
  await page.keyboard.press("Enter");
  await expect(crumbs(page)).toContainText(/Route\s*›\s*Attention mixes tokens/);
  await settled(page, "Stop 1, Eq. 1, The attention formula");
  await crumbs(page).getByRole("button", { name: "Route" }).click();
  await settled(page, "Stop 9,");
  await expect(current(page, stop(page, 3))).toBeVisible();

  await stop(page, 2).getByRole("button", { name: /Jump in/ }).click();
  await settled(page, "Stop 1, §2.1, Attention");
  await crumbs(page).getByRole("button", { name: "Back one level" }).click();
  await settled(page, "Stop 9,");
  await expect(current(page, stop(page, 2))).toBeVisible();

  // A stop with nothing inside doesn't jump.
  await stop(page, 5).dblclick();
  await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Route");
});

test("Show on Chart from a stop, and Find on Route from a concept", async ({ page }) => {
  await openRoute(page);

  // Stop 5 is Softmax, which lives one level down the Chart (Toyformer › Self-attention).
  await stop(page, 5).click();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Softmax" })).toBeVisible();
  await drawer(page).getByRole("button", { name: "Show on Chart" }).click();
  await expect(tab(page, "Chart")).toHaveAttribute("aria-selected", "true");
  await expect(chartCrumbs(page)).toContainText(/Toyformer\s*›\s*Self-attention/);
  await expect(chartNode(page, "Softmax").locator("[data-state*=selected]")).toBeVisible();
  await expect(drawer(page).getByRole("heading", { level: 2, name: "Softmax" })).toBeVisible();

  // And back: Find on Route makes that concept's stop current.
  await drawer(page).getByRole("button", { name: "Find on Route" }).click();
  await expect(tab(page, "Route")).toHaveAttribute("aria-selected", "true");
  await expect(current(page, stop(page, 5))).toBeVisible();

  // A concept that isn't on the route says so instead of doing nothing.
  await tab(page, "Chart").click();
  await chartCrumbs(page).getByRole("button", { name: "Toyformer" }).click();
  await chartNode(page, "Layer normalization").click();
  await drawer(page).getByRole("button", { name: "Find on Route" }).click();
  await expect(tab(page, "Route")).toHaveAttribute("aria-selected", "true");
  await expect(crumbs(page)).toContainText("This idea isn't on the main route.");
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("jumping into a stop swaps routes without animating", async ({ page }) => {
    await openRoute(page);
    // Selecting the stop prefetches its sub-route, so this measures the swap, not the network.
    await stop(page, 2).click();
    await page.waitForTimeout(600);

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
          const el = document.querySelector(`.react-flow__node[aria-label^="${label}"]`);
          if (start && el && opacity(el) === 1) resolve(performance.now() - start);
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    }, "Stop 1, §2.1, Attention");
    await stop(page, 2).dblclick();
    const ms = await page.evaluate(() => (window as unknown as { __jump: Promise<number> }).__jump);
    test.info().annotations.push({ type: "route jump-in (reduced motion)", description: `${Math.round(ms)} ms` });
    console.log(`[perf] route jump-in with reduced motion: ${Math.round(ms)} ms`);
    expect(ms).toBeLessThan(350); // the animated path takes ~600 ms (spec §13.4)

    const running = await page.evaluate(
      () => document.getAnimations().filter((a) => a.playState === "running" && a.effect?.getTiming().duration !== 0).length,
    );
    expect(running).toBe(0);
    await expect(crumbs(page)).toContainText(/Route\s*›\s*Three working parts/);

    await page.keyboard.press("Escape");
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Route");
    await expect(stop(page, 9)).toBeVisible();
  });
});
