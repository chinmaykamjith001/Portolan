// Bundle budget (spec §13.11): first-load JS under 400 KB gzipped, and the heavy parts (Chart,
// Route, FAQ, KaTeX) in lazy chunks that index.html never loads. Run after `vite build`
// (`pnpm build` does); `--dist DIR` checks another build output.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const BUDGET = 400 * 1000; // bytes, gzipped; "KB" as Vite prints it
const LAZY = ["ChartView", "RouteView", "FaqView", "katex"];

const argDist = process.argv.indexOf("--dist");
const dist = argDist > 0 ? process.argv[argDist + 1] : "dist";
const html = readFileSync(join(dist, "index.html"), "utf8");

const refs = [...html.matchAll(/(?:src|href)="\/?(assets\/[^"]+)"/g)].map((m) => m[1]);
const gz = (file) => gzipSync(readFileSync(join(dist, file))).length;
const kb = (n) => `${(n / 1000).toFixed(1)} KB`;

const firstJs = refs.filter((f) => f.endsWith(".js"));
const firstCss = refs.filter((f) => f.endsWith(".css"));
const jsBytes = firstJs.reduce((n, f) => n + gz(f), 0);
const cssBytes = firstCss.reduce((n, f) => n + gz(f), 0);

const assets = readdirSync(join(dist, "assets"));
const failures = [];

console.log(`first-load JS   ${kb(jsBytes)} gzipped (budget ${kb(BUDGET)})  ${firstJs.join(", ")}`);
console.log(`first-load CSS  ${kb(cssBytes)} gzipped  ${firstCss.join(", ")}`);
if (jsBytes > BUDGET) failures.push(`first-load JS is ${kb(jsBytes)}, over the ${kb(BUDGET)} budget`);

for (const name of LAZY) {
  const chunk = assets.find((f) => f.startsWith(`${name}-`) && f.endsWith(".js"));
  if (!chunk) {
    failures.push(`no separate chunk for ${name}: it would load with the first page`);
    continue;
  }
  if (firstJs.includes(`assets/${chunk}`)) failures.push(`${name} is loaded by index.html (${chunk})`);
  console.log(`lazy chunk      ${name.padEnd(10)} ${kb(gz(`assets/${chunk}`)).padStart(9)} gzipped  ${chunk}`);
}

if (failures.length > 0) {
  for (const f of failures) console.error(`FAIL ${f}`);
  process.exit(1);
}
console.log("size budget ok");
