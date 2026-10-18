import type { LiveState } from "../api/hooks";
import type { PaperInfo } from "../api/types";
import { Tabs } from "../design/primitives";
import { FindingsControl } from "../findings/FindingsControl";
import { useUI, type View } from "../state/store";
import styles from "./shell.module.css";

const STAGES = [
  { id: "reading", label: "Reading paper" },
  { id: "finding_code", label: "Finding code" },
  { id: "building_map", label: "Building map" },
] as const;

function Progress({ live, paper }: { live: LiveState; paper: PaperInfo | null | undefined }) {
  const stage = live.progress?.stage;
  if (paper?.chart_ready) return null; // later stages (code, findings) report in their own places
  const now = STAGES.findIndex((s) => s.id === stage);
  return (
    <span className={styles.progress} aria-live="polite">
      {STAGES.map((s, i) => (
        <span key={s.id} className={i < now ? styles.stageDone : i === now ? styles.stageNow : undefined}>
          {i > 0 && "→ "}
          {s.label}
        </span>
      ))}
    </span>
  );
}

export function PaperStrip({ paper, live }: { paper: PaperInfo | null | undefined; live: LiveState }) {
  const view = useUI((s) => s.view);
  const setView = useUI((s) => s.setView);
  const openDialog = useUI((s) => s.openDialog);
  const waiting = live.rateLimitedUntil !== null && live.rateLimitedUntil > Date.now();
  const seconds = waiting ? Math.ceil(((live.rateLimitedUntil ?? 0) - Date.now()) / 1000) : 0;
  const authors = paper?.authors ?? [];
  const failure = Object.values(paper?.failures ?? {})[0];
  return (
    <section className={styles.strip} aria-label="Paper">
      <h1 className={styles.title}>{paper?.title ?? "Reading paper…"}</h1>
      {authors.length > 0 && (
        <span className={styles.meta}>
          {authors.slice(0, 4).join(", ")}
          {authors.length > 4 ? ` +${authors.length - 4}` : ""}
        </span>
      )}
      {paper?.arxiv_id && (
        <span className={styles.mono}>
          arXiv:{paper.arxiv_id}
          {paper.version ?? ""}
        </span>
      )}
      {paper && paper.status !== "processing" && (
        <span className={styles.meta}>
          {paper.repo
            ? `Code: ${paper.repo.owner}/${paper.repo.name}`
            : (paper.repo_status?.message ?? "No code linked yet")}{" "}
          <button type="button" className={styles.linkButton} onClick={() => openDialog("code")}>
            change
          </button>
        </span>
      )}
      <FindingsControl paper={paper} />
      {paper?.low_structure && <span className={styles.notice}>Parsed from PDF; structure may be approximate.</span>}
      <div className={styles.views}>
        <Tabs<View>
          label="Views"
          value={view}
          onChange={setView}
          items={[
            { id: "chart", label: "Chart" },
            { id: "route", label: "Route" },
            { id: "faq", label: "FAQ" },
          ]}
        />
        <Progress live={live} paper={paper} />
        {/* Always in the page, so a screen reader announces the text when it appears. They are the
            last items in the row: empty, they take no visible space. */}
        <span className={styles.notice} role="status">
          {waiting ? `Waiting for the free-tier rate limit (~${seconds} s)` : ""}
        </span>
        <span className={styles.notice} role="alert">
          {live.lastError ?? failure ?? ""}
        </span>
      </div>
    </section>
  );
}
