// Performance budget (spec §13.11, M5 acceptance): pan a 300-node layer at 60 fps.
// Uses the dev-only page e2e/perf.html (the Chart canvas alone with a synthetic layer).
// Headless Chromium in CI/containers has no GPU, so the bar here is a median of ≥ 50 fps.

import { expect, test } from "@playwright/test";

const MIN_MEDIAN_FPS = 50;

test("panning a 300-node layer stays smooth", async ({ page }) => {
  await page.goto("/e2e/perf.html?n=300");
  await expect(page.locator(".react-flow__node")).toHaveCount(300, { timeout: 30_000 });
  await page.waitForTimeout(1500); // layout and the staggered fade-in settle

  // Find a spot of empty canvas to drag from (dragging starts a pan only on the pane).
  const start = await page.evaluate(() => {
    for (let y = 120; y < innerHeight - 80; y += 20) {
      for (let x = 40; x < innerWidth - 40; x += 20) {
        if (document.elementFromPoint(x, y)?.classList.contains("react-flow__pane")) return { x, y };
      }
    }
    return null;
  });
  expect(start, "no empty canvas to drag").not.toBeNull();
  const { x, y } = start ?? { x: 0, y: 0 };
  const transform = () =>
    page.evaluate(() => getComputedStyle(document.querySelector(".react-flow__viewport") as Element).transform);
  const before = await transform();

  await page.evaluate(() => {
    const w = window as unknown as { __frames: number[]; __recording: boolean };
    w.__frames = [];
    w.__recording = true;
    const tick = (t: number) => {
      if (!w.__recording) return;
      w.__frames.push(t);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  // ~1 s of dragging: a small step every frame, in a loop so the pan keeps going.
  await page.mouse.move(x, y);
  await page.mouse.down();
  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * Math.PI * 2;
    await page.mouse.move(x + 120 * Math.sin(a), y + 60 * (1 - Math.cos(a)));
    await page.waitForTimeout(16);
  }
  await page.mouse.up();

  const frames = await page.evaluate(() => {
    const w = window as unknown as { __frames: number[]; __recording: boolean };
    w.__recording = false;
    return w.__frames;
  });
  expect(await transform(), "the drag didn't pan the canvas").not.toBe(before);

  const intervals = frames.slice(1).map((t, i) => t - (frames[i] ?? t)).sort((a, b) => a - b);
  const median = intervals[Math.floor(intervals.length / 2)] ?? Infinity;
  const p95 = intervals[Math.floor(intervals.length * 0.95)] ?? Infinity;
  const fps = 1000 / median;
  test.info().annotations.push({
    type: "pan fps (300 nodes)",
    description: `median ${fps.toFixed(1)} fps, p95 frame ${p95.toFixed(1)} ms, ${frames.length} frames`,
  });
  console.log(`[perf] 300 nodes: median ${fps.toFixed(1)} fps, p95 frame ${p95.toFixed(1)} ms over ${frames.length} frames`);
  expect(frames.length).toBeGreaterThan(30);
  expect(fps).toBeGreaterThanOrEqual(MIN_MEDIAN_FPS);
});
