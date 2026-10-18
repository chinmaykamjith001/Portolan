// Renders the Chart canvas alone with a synthetic layer (default 300 nodes; ?n= to change).
// Served by the Vite dev server at /e2e/perf.html for e2e/perf.spec.ts; never part of the build.

import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "../src/design/global.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";

import { keys } from "../src/api/client";
import { ChartCanvas } from "../src/chart/ChartView";
import { syntheticLayer } from "../src/chart/synthetic";
import { useUI } from "../src/state/store";

const PAPER = "synthetic";
const n = Number(new URLSearchParams(window.location.search).get("n") ?? 300);
const payload = syntheticLayer(n);

const qc = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
qc.setQueryData(keys.layer(PAPER, "master"), payload);
useUI.getState().setPaper(PAPER);

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");
root.style.height = "100vh";

createRoot(root).render(
  <QueryClientProvider client={qc}>
    <ChartCanvas paperId={PAPER} layerId="master" payload={payload} />
  </QueryClientProvider>,
);
