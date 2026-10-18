// WCAG contrast of the design tokens (spec §13.12, §14.1), computed from tokens.css in BOTH
// themes. Text needs 4.5:1 (3:1 at 24 px and up); selection rings and outlines need 3:1.

import { readFileSync } from "node:fs";
import { join } from "node:path";

const CSS = readFileSync(join(__dirname, "..", "src", "design", "tokens.css"), "utf8");

function block(selector: string): string {
  const start = CSS.indexOf(selector);
  if (start < 0) throw new Error(`no ${selector} in tokens.css`);
  const open = CSS.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < CSS.length; i++) {
    if (CSS[i] === "{") depth++;
    if (CSS[i] === "}" && --depth === 0) return CSS.slice(open + 1, i);
  }
  throw new Error(`unclosed ${selector}`);
}

function colors(text: string): Record<string, string> {
  return Object.fromEntries([...text.matchAll(/--([a-z0-9-]+)\s*:\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1], m[2]]));
}

const LIGHT = colors(block(":root"));
const DARK = { ...LIGHT, ...colors(block('[data-theme="dark"]')) };
const THEMES = { light: LIGHT, dark: DARK } as const;

function channel(hex: string, i: number): number {
  const c = parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  return 0.2126 * channel(hex, 0) + 0.7152 * channel(hex, 1) + 0.0722 * channel(hex, 2);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT = 4.5;
const LARGE_TEXT = 3;
const NON_TEXT = 3;

// [foreground, background, minimum, where it is used]
const PAIRS: [string, string, number, string][] = [
  ["ink", "bg", TEXT, "body text on the canvas"],
  ["ink", "surface", TEXT, "text in the drawer, popovers, nodes"],
  ["ink-2", "bg", TEXT, "secondary text on the canvas"],
  ["ink-2", "surface", TEXT, "secondary text on surfaces"],
  ["ink-3", "surface", TEXT, "muted labels on surfaces"],
  ["ink-3", "bg", TEXT, "edge labels and muted text on the canvas (12 px)"],
  ["mismatch", "surface", TEXT, "mismatch text on surfaces"],
  ["mismatch", "bg", TEXT, "mismatch text on the canvas"],
  ["mismatch", "mismatch-soft", TEXT, "mismatch text in a mismatch box"],
  ["ink", "mismatch-soft", TEXT, "evidence text in a mismatch box"],
  ["accent", "bg", NON_TEXT, "selection outline on the canvas"],
  ["accent", "surface", NON_TEXT, "selection outline on a node"],
  ["code-ink", "code-bg", TEXT, "code"],
  ["code-dim", "code-bg", TEXT, "dimmed code and line numbers"],
  ["code-ink", "code-hl", TEXT, "highlighted code line"],
  ["code-dim", "code-hl", TEXT, "line numbers on a highlighted line"],
  ["code-ink", "code-hl-mismatch", TEXT, "mismatch code line"],
  ["code-dim", "code-hl-mismatch", TEXT, "line numbers on a mismatch line"],
];

describe.each(Object.entries(THEMES))("%s theme contrast", (_name, theme) => {
  test.each(PAIRS)("%s on %s ≥ %s (%s)", (fg, bg, min) => {
    const f = theme[fg];
    const b = theme[bg];
    expect(f, `--${fg} is defined`).toBeDefined();
    expect(b, `--${bg} is defined`).toBeDefined();
    expect(contrast(f as string, b as string)).toBeGreaterThanOrEqual(min);
  });
});

test("the large-text threshold is below the small-text one", () => {
  expect(LARGE_TEXT).toBeLessThan(TEXT);
});

test("the system-dark media block matches the manual dark theme", () => {
  const media = colors(block("@media (prefers-color-scheme: dark)"));
  const manual = colors(block('[data-theme="dark"]'));
  for (const [name, value] of Object.entries(manual)) expect(media[name], `--${name}`).toBe(value);
});

test("the contrast helper agrees with known values", () => {
  expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 1);
  expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
});
