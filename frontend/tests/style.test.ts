// Design guard (spec §14.4 and the M5 acceptance check): no gradients, no blur, only token colors.
// Scans every stylesheet and component under src/. Colors may be written as literals only in
// src/design/tokens.css; everything else uses var(--token). Failures list file:line.

// Read from disk: Vitest's CSS handling hides stylesheet text from `?raw` imports.
// Type-checked with tsconfig.node.json (Node types stay out of the app's tsconfig).
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = join(__dirname, "..");
const SRC = join(ROOT, "src");
const TOKENS = join(SRC, "design", "tokens.css");

const NAMED_COLORS = [
  "aqua", "azure", "beige", "black", "blue", "brown", "chartreuse", "coral", "crimson", "cyan",
  "fuchsia", "gold", "gray", "green", "grey", "hotpink", "indigo", "ivory", "khaki", "lavender",
  "lightblue", "lightgray", "lightgrey", "lime", "magenta", "maroon", "navy", "olive", "orange",
  "orchid", "pink", "plum", "purple", "red", "salmon", "silver", "tan", "teal", "tomato",
  "turquoise", "violet", "wheat", "white", "whitesmoke", "yellow",
];
const TAILWIND_HUES =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const TAILWIND_PREFIXES =
  "bg|text|border|ring|fill|stroke|from|to|via|shadow|outline|divide|placeholder|accent|caret|decoration";

interface Rule {
  name: string;
  test: (line: string) => boolean;
  /** Literal colors are fine in tokens.css only. */
  tokensExempt?: boolean;
}

const COLOR_PROP = /(?:^|[\s;{])(?:color|background(?:-color)?|border(?:-[a-z]+)*|outline(?:-color)?|fill|stroke|box-shadow|text-shadow|caret-color|accent-color|text-decoration(?:-color)?|column-rule)\s*:([^;]*)/i;
const JSX_COLOR = /\b(?:fill|stroke|color|background|backgroundColor|borderColor|outlineColor|stopColor|floodColor|lightingColor)\s*[:=]\s*\{?\s*["'`]([^"'`]*)["'`]/;
const namedColor = new RegExp(`(?:^|[\\s,(])(?:${NAMED_COLORS.join("|")})(?=$|[\\s,);!])`, "i");

const RULES: Rule[] = [
  { name: "gradient", test: (l) => /gradient\s*\(/i.test(l) },
  { name: "blur", test: (l) => /\bblur\s*\(/i.test(l) || /backdrop-filter|backdropFilter/.test(l) },
  {
    name: "color literal (use a token)",
    tokensExempt: true,
    test: (l) =>
      /(?:^|[\s"'`:(,])#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})\b/i.test(l) ||
      /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(/i.test(l),
  },
  {
    name: "named color (use a token)",
    tokensExempt: true,
    test: (l) => {
      const css = COLOR_PROP.exec(l)?.[1];
      const jsx = JSX_COLOR.exec(l)?.[1];
      return [css, jsx].some((v) => v !== undefined && namedColor.test(` ${v}`));
    },
  },
  { name: "Inter font", test: (l) => /font-?family\s*[:=][^;]*\bInter\b/i.test(l) },
  {
    name: "Tailwind default palette class",
    test: (l) => new RegExp(`\\b(?:${TAILWIND_PREFIXES})-(?:${TAILWIND_HUES})-\\d{2,3}\\b`).test(l),
  },
  {
    name: "font weight other than 400/500/600",
    test: (l) => {
      const m = /font-?weight\s*:\s*["']?([a-z0-9]+)/i.exec(l);
      return m !== null && !["400", "500", "600", "normal", "inherit"].includes(m[1] ?? "");
    },
  },
  // Text-default symbols such as ↘ are fine; characters that render as emoji are not.
  { name: "emoji", test: (l) => /\p{Emoji_Presentation}|️/u.test(l) },
];

export interface Violation {
  file: string;
  line: number;
  rule: string;
  text: string;
}

/** Blank out comments, keeping line numbers. */
function stripComments(text: string, file: string): string {
  const noBlocks = text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  if (file.endsWith(".css")) return noBlocks;
  return noBlocks.replace(/^\s*\/\/.*$/gm, "");
}

export function checkSource(file: string, text: string, isTokens = false): Violation[] {
  const out: Violation[] = [];
  stripComments(text, file)
    .split("\n")
    .forEach((line, i) => {
      for (const rule of RULES) {
        if (isTokens && rule.tokensExempt) continue;
        if (rule.test(line)) out.push({ file, line: i + 1, rule: rule.name, text: line.trim() });
      }
    });
  return out;
}

/** Every var(--x) must be a token or a custom property defined in our own sources. */
export function undefinedVars(sources: { file: string; text: string }[]): Violation[] {
  const defined = new Set<string>();
  for (const { text } of sources) {
    for (const m of text.matchAll(/(--[\w-]+)\s*:/g)) defined.add(m[1] ?? "");
    for (const m of text.matchAll(/["'](--[\w-]+)["']\s*:/g)) defined.add(m[1] ?? ""); // style={{"--x": …}}
  }
  const out: Violation[] = [];
  for (const { file, text } of sources) {
    stripComments(text, file)
      .split("\n")
      .forEach((line, i) => {
        for (const m of line.matchAll(/var\(\s*(--[\w-]+)/g)) {
          const name = m[1] ?? "";
          if (!defined.has(name) && !name.startsWith("--xy-")) {
            out.push({ file, line: i + 1, rule: `undefined custom property ${name}`, text: line.trim() });
          }
        }
      });
  }
  return out;
}

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return walk(p);
    return /\.(css|tsx?)$/.test(e.name) ? [p] : [];
  });
}

const format = (vs: Violation[]) =>
  vs.map((v) => `  ${relative(ROOT, v.file)}:${v.line}  ${v.rule}\n      ${v.text}`).join("\n");

test("the checker catches what §14.4 forbids", () => {
  const bad = [
    ".a { background: linear-gradient(red, blue); }",
    ".a { backdrop-filter: blur(4px); }",
    ".a { color: #2F4F8F; }",
    ".a { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); }",
    ".a { border: 1px solid white; }",
    ".a { font-family: Inter, sans-serif; }",
    ".a { font-weight: 700; }",
  ];
  for (const css of bad) expect(checkSource("x.css", css), css).not.toHaveLength(0);
  const badTsx = [
    '<div className="bg-indigo-500" />',
    '<path fill="#fff" />',
    '<circle stroke="red" />',
    "const s = { color: 'black' };",
    'const label = "Map ready ✨";',
  ];
  for (const tsx of badTsx) expect(checkSource("x.tsx", tsx), tsx).not.toHaveLength(0);

  const good = [
    ".a { color: var(--ink); border: 1px solid var(--line); background: transparent; }",
    ".a { box-shadow: 0 2px 12px var(--shadow); font-weight: 600; }",
    "/* never a gradient( here */ .a { fill: none; stroke: currentColor; }",
    ".b { border-bottom: 2px solid transparent; }",
  ];
  for (const css of good) expect(checkSource("x.css", css), css).toEqual([]);
  const goodTsx = ['<span aria-label="Jump in">↘ 3</span>', '<a href="#top">Top</a>', '<rect fill="none" />'];
  for (const tsx of goodTsx) expect(checkSource("x.tsx", tsx), tsx).toEqual([]);

  expect(undefinedVars([{ file: "a.css", text: ".a { color: var(--nope); }" }])).toHaveLength(1);
});

test("src/ uses no forbidden styles and only token colors", () => {
  const sources = walk(SRC).map((file) => ({ file, text: readFileSync(file, "utf8") }));
  expect(sources.some((s) => s.file === TOKENS)).toBe(true);
  const violations = [
    ...sources.flatMap(({ file, text }) => checkSource(file, text, file === TOKENS)),
    ...undefinedVars(sources),
  ];
  expect(violations, `Forbidden styles (spec §14.4):\n${format(violations)}`).toEqual([]);
});
