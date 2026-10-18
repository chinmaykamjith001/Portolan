// Keyboard focus must always be visible (spec §13.12). The only places allowed to remove the
// outline are headings and regions that are focused from code (tabindex="-1"), never anything a
// user tabs to: those must not turn the outline off under :focus-visible.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = join(__dirname, "..", "src");

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? cssFiles(p) : p.endsWith(".css") ? [p] : [];
  });
}

/** Selectors that are focused only by code (tabindex="-1"): removing their outline is fine. */
const PROGRAMMATIC = [/\.title:focus$/, /\.main:focus$/];

test("nothing a user tabs to removes its :focus-visible outline", () => {
  const bad: string[] = [];
  for (const file of cssFiles(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = (m[1] ?? "").trim();
      const body = m[2] ?? "";
      if (!/outline\s*:\s*(none|0)\b/.test(body)) continue;
      if (PROGRAMMATIC.some((re) => re.test(selector))) continue;
      const line = text.slice(0, m.index).split("\n").length;
      bad.push(`${relative(SRC, file)}:${line} ${selector}`);
    }
  }
  expect(bad).toEqual([]);
});

test("global focus style exists and uses the accent token", () => {
  const css = readFileSync(join(SRC, "design", "global.css"), "utf8");
  expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--accent\)/);
});
