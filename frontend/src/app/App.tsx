import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";

import { api } from "../api/client";
import { usePaper, usePaperEvents } from "../api/hooks";
import type { TitleCandidate } from "../api/types";
import { DetailDrawer } from "../detail/DetailDrawer";
import { FindingsPanel } from "../findings/FindingsPanel";
import { useUI } from "../state/store";
import { PaperStrip } from "./PaperStrip";
import styles from "./shell.module.css";
import { TopBar, type Theme } from "./TopBar";

const ChartView = lazy(() => import("../chart/ChartView"));
const RouteView = lazy(() => import("../route/RouteView"));
const FaqView = lazy(() => import("../faq/FaqView"));

const EXAMPLES = [
  { label: "Attention Is All You Need", input: "1706.03762" },
  { label: "LoRA: Low-Rank Adaptation of Large Language Models", input: "2106.09685" },
  { label: "Denoising Diffusion Probabilistic Models", input: "2006.11239" },
];

const THEME_KEY = "portolan.theme";

function readTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // private mode: the choice just isn't remembered
    }
  }, [theme]);
  return [theme, setTheme];
}

/** Keep ?paper= in the URL so a reload reopens the same paper. */
function useUrlSync() {
  const paperId = useUI((s) => s.paperId);
  const setPaper = useUI((s) => s.setPaper);
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("paper");
    if (fromUrl) setPaper(fromUrl);
  }, [setPaper]);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (paperId) url.searchParams.set("paper", paperId);
    else url.searchParams.delete("paper");
    window.history.replaceState(null, "", url);
  }, [paperId]);
}

function Empty({ open }: { open: (input: string) => void }) {
  return (
    <div className={styles.empty}>
      <p>Paste a paper to map its ideas, follow it in the order it was written, and see where each idea lives in the code.</p>
      <ul>
        {EXAMPLES.map((e) => (
          <li key={e.input}>
            <button type="button" className={styles.linkButton} onClick={() => open(e.input)}>
              {e.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Candidates({ items, pick }: { items: TitleCandidate[]; pick: (c: TitleCandidate) => void }) {
  return (
    <div className={styles.message}>
      <p>Which paper did you mean?</p>
      <ul>
        {items.map((c) => (
          <li key={c.arxiv_id}>
            <button type="button" className={styles.linkButton} onClick={() => pick(c)}>
              {c.title}
            </button>{" "}
            <span className={styles.meta}>
              {c.authors.slice(0, 3).join(", ")} ({c.year})
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Shell() {
  useUrlSync();
  const [theme, setTheme] = useTheme();
  const paperId = useUI((s) => s.paperId);
  const view = useUI((s) => s.view);
  const setPaper = useUI((s) => s.setPaper);
  const escape = useUI((s) => s.escape);
  const [candidates, setCandidates] = useState<TitleCandidate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const live = usePaperEvents(paperId);
  const { data: paper } = usePaper(paperId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target instanceof HTMLInputElement)) escape();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [escape]);

  async function open(input: string) {
    setError(null);
    try {
      const res = await api.createPaper(input);
      if (!res.pending && "paper_id" in res.data) setPaper(res.data.paper_id);
    } catch {
      setError("Couldn't reach the Portolan server. Is it running?");
    }
  }

  return (
    <div className={styles.app}>
      <TopBar onCandidates={setCandidates} onError={setError} theme={theme} setTheme={setTheme} />
      {paperId && <PaperStrip paper={paper} live={live} />}
      <main className={styles.main}>
        {error && <p className={styles.message}>{error}</p>}
        {!error && candidates.length > 0 && (
          <Candidates
            items={candidates}
            pick={(c) => {
              setCandidates([]);
              void open(`${c.arxiv_id}${c.version ?? ""}`);
            }}
          />
        )}
        {!error && candidates.length === 0 && !paperId && <Empty open={(i) => void open(i)} />}
        {paperId && view === "chart" && (
          <Suspense fallback={null}>
            <ChartView />
          </Suspense>
        )}
        {paperId && view === "route" && (
          <Suspense fallback={null}>
            <RouteView />
          </Suspense>
        )}
        {paperId && view === "faq" && (
          <Suspense fallback={null}>
            <FaqView />
          </Suspense>
        )}
        {paperId && <FindingsPanel />}
        {paperId && <DetailDrawer />}
      </main>
    </div>
  );
}

export function App({ client }: { client?: QueryClient }) {
  const [qc] = useState(() => client ?? new QueryClient({ defaultOptions: { queries: { retry: 1 } } }));
  return (
    <QueryClientProvider client={qc}>
      <Shell />
    </QueryClientProvider>
  );
}
