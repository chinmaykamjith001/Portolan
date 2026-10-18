/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // PORTOLAN_API_URL: the e2e tests run their own backend on another port.
    proxy: { "/api": process.env.PORTOLAN_API_URL ?? "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    exclude: [...configDefaults.exclude, "e2e/**"], // Playwright specs
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
