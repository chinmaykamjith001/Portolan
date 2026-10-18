// M11: keyboard, Settings, dark mode, first-run states, and the performance budgets (spec §13.10–
// §13.12, §13.11). Everything runs against the real backend (mock provider, seeded demo paper).
// The budget tests use the PRODUCTION build served by `vite preview` (see playwright.config.ts);
// they are skipped with PW_NO_PREVIEW=1. Numbers come from a headless, software-rendered
// container, so they are an upper bound for a real laptop, not a lab result.

import { expect, test, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";
const PREVIEW = `http://127.0.0.1:${process.env.PW_PREVIEW_PORT ?? 4174}`;
const HAS_PREVIEW = process.env.PW_NO_PREVIEW !== "1";

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const node = (page: Page, title: string) =>
  page.locator(".react-flow__node").and(page.getByRole("group", { name: new RegExp(`^${escapeRe(title)},`) }));
const drawer = (page: Page) => page.getByRole("complementary", { name: "Concept details" });
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Where you are on the map" });

async function openDemo(page: Page) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(page.getByRole("heading", { level: 1, name: /Toyformer/ })).toBeVisible();
  await expect(node(page, "Self-attention")).toBeVisible();
}

const current = (page: Page) => page.locator(".react-flow__node[aria-current='true']");

// ---------------------------------------------------------------------------------------------
// Keyboard (§13.10)

test.describe("keyboard", () => {
  test("? lists the shortcuts and Esc closes the list", async ({ page }) => {
    await openDemo(page);
    await page.keyboard.press("?");
    const dialog = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    await expect(dialog).toBeVisible();
    for (const text of ["Fit the view", "Search this paper", "Previous or next stop"]) {
      await expect(dialog).toContainText(text);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    // Esc went to the dialog, not to "go back": still on the master map.
    await expect(node(page, "Self-attention")).toBeVisible();
  });

  test("/ searches the loaded concepts and Enter shows the match on the Chart", async ({ page }) => {
    await openDemo(page);
    await page.keyboard.press("/");
    const box = page.getByRole("combobox", { name: "Search concepts" });
    await expect(box).toBeFocused();
    await box.fill("self");
    await expect(page.getByRole("option", { name: /Self-attention/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(drawer(page).getByRole("heading", { level: 2, name: "Self-attention" })).toBeVisible();
    await expect(current(page)).toHaveAttribute("aria-label", /^Self-attention,/);
  });

  test("shortcuts stay quiet while typing and while a dialog is open", async ({ page }) => {
    await openDemo(page);
    await page.getByRole("textbox", { name: "Paper" }).focus();
    await page.keyboard.type("what?/");
    await expect(page.getByRole("textbox", { name: "Paper" })).toHaveValue("what?/");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "Settings" }).click();
    await page.keyboard.press("?");
    await page.keyboard.press("/");
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(page.getByRole("dialog")).toHaveAccessibleName("Settings");
  });

  test("Tab and Shift+Tab move the selection between concepts, Enter jumps in", async ({ page }) => {
    await openDemo(page);
    await node(page, "Self-attention").click();
    await expect(current(page)).toHaveAttribute("aria-label", /^Self-attention,/);
    await page.keyboard.press("Tab");
    await expect(current(page)).not.toHaveAttribute("aria-label", /^Self-attention,/);
    await page.keyboard.press("Shift+Tab");
    await expect(current(page)).toHaveAttribute("aria-label", /^Self-attention,/);
    await expect(current(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
  });

  test("Esc closes the drawer before it goes back a level", async ({ page }) => {
    await openDemo(page);
    await node(page, "Self-attention").dblclick();
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
    await node(page, "Scaled dot-product").click();
    await expect(drawer(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer(page)).toHaveCount(0);
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Self-attention");
    await page.keyboard.press("Escape");
    await expect(crumbs(page).locator("[aria-current=location]")).toHaveText("Toyformer"); // the top
  });

  test("the skip link is the first stop and moves focus into the page", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();
  });
});

// ---------------------------------------------------------------------------------------------
// Settings

test.describe("settings", () => {
  test("a labelled dialog that traps Tab, closes on Esc and returns focus", async ({ page }) => {
    await page.goto("/");
    const opener = page.getByRole("button", { name: "Settings" });
    await opener.click();
    const dialog = page.getByRole("dialog", { name: "Settings" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Provider");
    await expect(dialog).toContainText("mock"); // the e2e backend runs the mock provider
    await expect(dialog).toContainText("Edit .env on the machine running Portolan; the key is never shown here.");
    await expect(dialog).not.toContainText("Free Gemini keys"); // only for a Gemini provider
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    for (let i = 0; i < 4; i++) {
      await page.keyboard.press("Shift+Tab");
      expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
  });

  test("the key is never in the page", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Settings" }).click();
    const text = await page.locator("body").innerText();
    expect(text).not.toMatch(/AIza[0-9A-Za-z_-]{10,}|GEMINI_API_KEY\s*=/);
  });
});

// ---------------------------------------------------------------------------------------------
// Dark mode (§14.1)

/** The computed colors the design says a theme must produce. */
async function themeColors(page: Page) {
  return page.evaluate(() => {
    const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const rgb = (hex: string) => {
      const n = parseInt(hex.slice(1), 16);
      return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
    };
    const cs = (el: Element | null) => (el ? getComputedStyle(el) : null);
    const body = cs(document.body);
    const nodeInner = cs(document.querySelector(".react-flow__node [data-grounding]"));
    const drawerEl = cs(document.querySelector("aside[aria-label='Concept details']"));
    return {
      bgToken: css("--bg"),
      inkToken: css("--ink"),
      surfaceToken: css("--surface"),
      bodyBg: body?.backgroundColor,
      bodyInk: body?.color,
      expectedBg: rgb(css("--bg")),
      expectedInk: rgb(css("--ink")),
      expectedSurface: rgb(css("--surface")),
      nodeInk: nodeInner?.color,
      drawerBg: drawerEl?.backgroundColor,
      colorScheme: getComputedStyle(document.documentElement).colorScheme,
    };
  });
}

test.describe("dark mode", () => {
  test.use({ colorScheme: "light" });

  test("the manual choice switches the body, a node and the drawer to the dark tokens, and persists", async ({
    page,
  }) => {
    await openDemo(page);
    await node(page, "Self-attention").click();
    await expect(drawer(page)).toBeVisible();
    const light = await themeColors(page);
    expect(light.bgToken.toLowerCase()).toBe("#f7f5f0");
    expect(light.bodyBg).toBe(light.expectedBg);
    expect(light.bodyInk).toBe(light.expectedInk);
    expect(light.nodeInk).toBe(light.expectedInk);
    expect(light.drawerBg).toBe(light.expectedSurface);
    expect(light.colorScheme).toBe("light");

    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByLabel("Theme").selectOption("dark");
    await page.keyboard.press("Escape");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    const dark = await themeColors(page);
    expect(dark.bgToken.toLowerCase()).toBe("#0f1318");
    expect(dark.inkToken.toLowerCase()).toBe("#ece8df");
    expect(dark.bodyBg).toBe(dark.expectedBg);
    expect(dark.bodyInk).toBe(dark.expectedInk);
    expect(dark.nodeInk).toBe(dark.expectedInk);
    expect(dark.drawerBg).toBe(dark.expectedSurface);
    expect(dark.colorScheme).toBe("dark");
    expect(dark.bodyBg).not.toBe(light.bodyBg);
    expect(dark.drawerBg).not.toBe(light.drawerBg);

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect((await themeColors(page)).bodyBg).toBe(dark.bodyBg);
  });

  test("following the system: a dark system gets the dark tokens without any choice", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "dark" });
    const page = await context.newPage();
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Paper" })).toBeVisible();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
    const c = await themeColors(page);
    expect(c.bgToken.toLowerCase()).toBe("#0f1318");
    expect(c.bodyBg).toBe(c.expectedBg);
    await context.close();
  });

  test("a manual Light wins over a dark system", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "dark" });
    const page = await context.newPage();
    await page.addInitScript(() => localStorage.setItem("portolan.theme", "light"));
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const c = await themeColors(page);
    expect(c.bgToken.toLowerCase()).toBe("#f7f5f0");
    expect(c.bodyBg).toBe(c.expectedBg);
    await context.close();
  });
});

// ---------------------------------------------------------------------------------------------
// First run (§13.9)

test.describe("first run", () => {
  test("the empty state is the input, one line of help and three example papers", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("textbox", { name: "Paper" })).toHaveAttribute("placeholder", "Paste an arXiv link, DOI, PDF or title");
    const main = page.locator("main");
    await expect(main.getByRole("button")).toHaveCount(3);
    await expect(main.locator("p")).toHaveCount(1);
    await expect(page.getByRole("alert")).toHaveCount(0); // the backend is up
  });

  test("a backend that isn't running says how to start it", async ({ page }) => {
    // Only the backend: Vite also serves source files under /src/api/.
    await page.route((url) => url.pathname.startsWith("/api/"), (route) => route.abort());
    await page.goto("/");
    await expect(page.getByRole("alert")).toHaveText("Couldn't reach the Portolan server. Start it with make dev.");
  });
});

// ---------------------------------------------------------------------------------------------
// Performance budgets (§13.11), on the production build

test.describe("budgets on the production build", () => {
  test.skip(!HAS_PREVIEW, "PW_NO_PREVIEW=1");
  test.use({ baseURL: PREVIEW });

  test("first paint under 1.5 s (median of 3 cold loads)", async ({ browser }) => {
    const samples: number[] = [];
    for (let i = 0; i < 3; i++) {
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.goto("/", { waitUntil: "load" });
      await expect(page.getByRole("textbox", { name: "Paper" })).toBeVisible();
      samples.push(
        await page.evaluate(
          () => performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? Number.NaN,
        ),
      );
      await context.close();
    }
    samples.sort((a, b) => a - b);
    const median = samples[1] ?? Number.NaN;
    console.log(`[perf] first contentful paint: median ${median.toFixed(0)} ms (samples ${samples.map((s) => s.toFixed(0)).join(", ")})`);
    expect(median).toBeLessThan(1500);
  });

  test("the first page loads one script; Chart, Route and KaTeX arrive when needed", async ({ page }) => {
    const scripts: string[] = [];
    page.on("request", (r) => {
      const m = /\/assets\/([^/?]+\.js)/.exec(r.url());
      if (m?.[1]) scripts.push(m[1]);
    });
    await page.goto("/", { waitUntil: "networkidle" });
    expect(scripts.map((s) => s.split("-")[0])).toEqual(["index"]);

    await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
    await expect(node(page, "Self-attention")).toBeVisible();
    expect(scripts.some((s) => s.startsWith("ChartView-"))).toBe(true);
    expect(scripts.some((s) => s.startsWith("RouteView-"))).toBe(false);
    expect(scripts.some((s) => s.startsWith("FaqView-"))).toBe(false);
    expect(scripts.some((s) => s.startsWith("katex-"))).toBe(false); // only when math is on screen
    // The big ELK build is for browsers without Workers; this one has them.
    expect(scripts.some((s) => s.startsWith("elk.bundled-"))).toBe(false);

    await page.getByRole("tab", { name: "Route" }).click();
    await expect.poll(() => scripts.some((s) => s.startsWith("RouteView-"))).toBe(true);
  });

  test("click to drawer in under 100 ms", async ({ page }) => {
    await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
    await expect(node(page, "Self-attention")).toBeVisible();
    await page.waitForTimeout(500); // let the first layout and the prefetches settle
    const samples: number[] = [];
    for (const title of ["Self-attention", "Positional encoding", "Layer normalization"]) {
      samples.push(
        await page.evaluate(async (label) => {
          const el = document.querySelector(`.react-flow__node[aria-label^="${label},"]`);
          if (!el) return Number.NaN;
          const t0 = performance.now();
          el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, detail: 1 }));
          return new Promise<number>((resolve) => {
            const tick = () => {
              const d = document.querySelector("aside[aria-label='Concept details']");
              const open = d?.getAttribute("aria-hidden") === "false";
              if (open && d?.querySelector("h2")?.textContent?.includes(label)) resolve(performance.now() - t0);
              else requestAnimationFrame(tick);
            };
            tick();
          });
        }, title),
      );
    }
    console.log(`[perf] click → drawer with title: ${samples.map((s) => `${s.toFixed(0)} ms`).join(", ")}`);
    for (const ms of samples) expect(ms).toBeLessThan(100);
  });

  test("no layout shift when the explanation arrives", async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __cls: number };
      w.__cls = 0;
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          const shift = e as PerformanceEntry & { value: number; hadRecentInput: boolean };
          if (!shift.hadRecentInput) w.__cls += shift.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    });
    // Slow the explanation down, so the skeleton is on screen first, as when it is streamed in.
    await page.route(/\/explanation\?angle=default/, async (route) => {
      await new Promise((r) => setTimeout(r, 700));
      await route.continue();
    });
    await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
    await expect(node(page, "Self-attention")).toBeVisible();
    await page.waitForTimeout(500);
    const before = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    await node(page, "Self-attention").click();
    await expect(drawer(page)).toContainText("each token builds its new representation", { timeout: 10_000 });
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
    const shift = after - before;
    console.log(`[perf] layout shift while the drawer opens and the explanation loads: ${shift.toFixed(4)}`);
    expect(shift).toBeLessThan(0.01);
  });
});
