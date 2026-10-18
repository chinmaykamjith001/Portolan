// The Findings list (spec §13.1): paper claims checked against the code. Opens from the paper
// strip; a floating panel over the canvas, not a grid of cards.

import { useEffect, useRef, useState } from "react";

import { useFindings, usePaper } from "../api/hooks";
import type { Finding } from "../api/types";
import { Button, Chip } from "../design/primitives";
import { anchorLabel } from "../detail/anchors";
import { useUI } from "../state/store";
import styles from "./findings.module.css";
import { confidenceWords, isProminent, sortFindings, spanHref, summarize, VERDICT_LABEL } from "./model";
import { VerdictGlyph } from "./VerdictGlyph";

function Row({ f, repoUrl, onShow }: { f: Finding; repoUrl: string | null; onShow: (conceptId: string) => void }) {
  const first = f.concept_ids[0];
  return (
    <li className={`${styles.row} ${f.verdict === "mismatch" ? styles.rowMismatch : ""}`} data-verdict={f.verdict}>
      <p className={styles.verdict}>
        <VerdictGlyph verdict={f.verdict} />
        <span>{VERDICT_LABEL[f.verdict]}</span>
        <span className={styles.confidence}>{confidenceWords(f.confidence)}</span>
      </p>
      <p className={styles.claim}>{f.claim_text}</p>
      <p className={styles.evidence}>{f.evidence}</p>
      <div className={styles.meta}>
        {f.paper_anchor && <Chip title={f.paper_anchor}>{anchorLabel(f.paper_anchor)}</Chip>}
        {f.code_spans.map((s) => {
          const label = `${s.path}:${s.start_line}${s.end_line > s.start_line ? `–${s.end_line}` : ""}`;
          const href = spanHref(repoUrl, s.commit_sha, s.path, s.start_line, s.end_line);
          return href ? (
            <a key={`${s.path}:${s.start_line}`} className={styles.span} href={href} target="_blank" rel="noreferrer">
              {label}
            </a>
          ) : (
            <span key={`${s.path}:${s.start_line}`} className={styles.span}>
              {label}
            </span>
          );
        })}
        {first !== undefined && (
          <Button quiet className={styles.show} onClick={() => onShow(first)}>
            Show on Chart
          </Button>
        )}
      </div>
    </li>
  );
}

export function FindingsPanel() {
  const paperId = useUI((s) => s.paperId);
  const open = useUI((s) => s.findingsOpen);
  const setOpen = useUI((s) => s.setFindingsOpen);
  const focusConcept = useUI((s) => s.focusConcept);
  const paper = usePaper(paperId).data ?? null;
  const q = useFindings(open ? paperId : null);
  const [showAll, setShowAll] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (open) heading.current?.focus({ preventScroll: true });
  }, [open]);

  if (!open || paperId === null) return null;

  const payload = q.data ?? null;
  const all = sortFindings(payload?.findings ?? []);
  const prominent = all.filter(isProminent);
  const rest = all.filter((f) => !isProminent(f));
  const shown = showAll ? all : prominent;
  const summary = summarize(payload, Boolean(paper?.repo));
  const repoUrl = paper?.repo?.url ?? null;

  function show(conceptId: string) {
    setOpen(false);
    focusConcept(conceptId, "chart");
  }

  return (
    <aside className={styles.panel} aria-label="Findings" role="dialog">
      <header className={styles.head}>
        <h2 ref={heading} tabIndex={-1} className={styles.title}>
          Findings
        </h2>
        <Button quiet aria-label="Close findings" onClick={() => setOpen(false)}>
          Close
        </Button>
      </header>
      <p className={styles.lead} aria-live="polite">
        {q.isError
          ? "Couldn't load the findings. Try again in a moment."
          : (summary?.text ?? (payload ? "Nothing has been checked yet." : "Loading…"))}
      </p>
      {shown.length > 0 && (
        <ul className={styles.list}>
          {shown.map((f) => (
            <Row key={f.id} f={f} repoUrl={repoUrl} onShow={show} />
          ))}
        </ul>
      )}
      {payload && prominent.length === 0 && payload.state === "done" && rest.length > 0 && !showAll && (
        <p className={styles.quiet}>Nothing in the code contradicts the paper. The checks below were routine.</p>
      )}
      {rest.length > 0 && (
        <button
          type="button"
          className={styles.toggle}
          aria-expanded={showAll}
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll
            ? "Hide lower-confidence and matching checks"
            : `Show ${rest.length} lower-confidence and matching ${rest.length === 1 ? "check" : "checks"}`}
        </button>
      )}
    </aside>
  );
}
