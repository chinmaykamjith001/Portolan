// End-to-end tests (spec §16, M5 acceptance). Runs the real backend under the mock provider on a
// fresh database seeded with the demo paper (`portolan seed-demo`: no network, no LLM), plus Vite.
//
//   pnpm e2e              # starts both servers, runs e2e/*.spec.ts in Chromium
//   PW_CHROMIUM=/path/to/chrome pnpm e2e   # use a specific Chromium build

import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { defineConfig, devices } from "@playwright/test";

const API_PORT = Number(process.env.PW_API_PORT ?? 8765); // override to run two suites at once
const WEB_PORT = Number(process.env.PW_WEB_PORT ?? 5174);

// One data dir per run. Workers inherit the env, so they don't create their own.
process.env.PORTOLAN_E2E_HOME ??= mkdtempSync(join(tmpdir(), "portolan-e2e-"));
const HOME = process.env.PORTOLAN_E2E_HOME;
// An empty config, so the owner's portolan.toml (models, limits) can't change what the tests see.
const CONFIG = join(HOME, "portolan.toml");
if (!existsSync(CONFIG)) writeFileSync(CONFIG, "");

// Containers sometimes ship a Chromium build other than the one this Playwright version expects.
const PREINSTALLED = "/opt/pw-browsers/chromium";
const executablePath = process.env.PW_CHROMIUM ?? (existsSync(PREINSTALLED) ? PREINSTALLED : undefined);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // one shared backend; specs change server-side state (queued jobs)
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], launchOptions: executablePath ? { executablePath } : {} },
    },
  ],
  webServer: [
    {
      command: `uv run --directory ../backend portolan seed-demo && uv run --directory ../backend portolan serve --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PORTOLAN_HOME: HOME,
        PORTOLAN_LLM_PROVIDER: "mock",
        PORTOLAN_NO_DOTENV: "1", // never load the owner's .env (and key) into the test server
        PORTOLAN_CONFIG: CONFIG,
      },
    },
    {
      command: `pnpm exec vite --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false, // its proxy must point at the test backend
      timeout: 120_000,
      env: { PORTOLAN_API_URL: `http://127.0.0.1:${API_PORT}` },
    },
  ],
});
