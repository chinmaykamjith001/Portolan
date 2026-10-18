// Code tab (spec §13.6): the lines that implement a concept, with an arrow callout under each
// annotated line. Mismatch is signalled by shape ("!") as well as colour (§13.12).

import { ApiError } from "../api/client";
import { useCode } from "../api/hooks";
import type { CodeGrounding, LineNote } from "../api/types";
import { Button, Shimmer } from "../design/primitives";
import { buildBlocks, KIND_LABEL, lineRange, type CodeBlock, type CodeLine } from "./code";
import styles from "./detail.module.css";
import type { Verdict } from "./DetailDrawer";
import { tokenize } from "./highlight";

function Arrow() {
  return (
    <svg className={styles.arrow} width="20" height="18" viewBox="0 0 20 18" aria-hidden="true">
      <path d="M18 15 H7 Q3 15 3 11 V4" fill="none" stroke="currentColor" strokeWidth="1.25" />
      <path d="M0.5 6.5 L3 3 L5.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.25" />
    </svg>
  );
}

function Callout({ note }: { note: LineNote }) {
  return (
    <div role="note" className={`${styles.callout} ${styles[`kind_${note.kind}`] ?? ""}`} data-kind={note.kind}>
      <Arrow />
      <p className={styles.calloutText}>
        {note.kind === "mismatch" && (
          <span className={styles.bang} aria-hidden="true">
            !
          </span>
        )}
        <span className={styles.srOnly}>{KIND_LABEL[note.kind]}: </span>
        <strong>{note.tag}</strong> {note.note}
      </p>
    </div>
  );
}

function rowKind(line: CodeLine): string {
  if (line.notes.some((n) => n.kind === "mismatch")) return styles.rowMismatch ?? "";
  if (line.notes.some((n) => n.kind !== "context")) return styles.rowNoted ?? "";
  return line.notes.length > 0 ? (styles.rowContext ?? "") : "";
}

function Block({ block }: { block: CodeBlock }) {
  const range = lineRange(block.start, block.end);
  return (
    <figure className={styles.block}>
      <figcaption className={styles.blockHead}>
        {block.href ? (
          <a className={styles.path} href={block.href} target="_blank" rel="noreferrer">
            {block.path}
          </a>
        ) : (
          <span className={styles.path}>{block.path}</span>
        )}
        <span className={styles.range}>{range}</span>
      </figcaption>
      <div
        className={`${styles.scroll} ${block.hasNotes ? styles.hasNotes : ""}`}
        tabIndex={0}
        role="region"
        aria-label={`${block.path}, ${range.toLowerCase()}`}
      >
        {block.lines.map((line) => (
          <div key={line.n} className={styles.lineGroup}>
            <div className={`${styles.row} ${rowKind(line)}`} data-line={line.n}>
              <span className={styles.num}>{line.n}</span>
              <span className={styles.bar} aria-hidden="true" />
              <span className={styles.text}>
                {tokenize(line.text, block.lang).map((t, i) => (
                  <span key={i} className={styles[`t_${t.kind}`]}>
                    {t.text}
                  </span>
                ))}
                {line.text === "" && " "}
              </span>
            </div>
            {line.notes.map((note, i) => (
              <Callout key={i} note={note} />
            ))}
          </div>
        ))}
      </div>
    </figure>
  );
}

/** Where the checker's verdict on this concept will go (M8); nothing to show until then. */
function VerdictBox({ verdict }: { verdict: Verdict | null }) {
  if (verdict === null) return null;
  return (
    <p className={verdict === "mismatch" ? styles.verdictBoxMismatch : styles.verdictBox}>
      {verdict === "mismatch" ? "! The code differs from the paper here." : "The code matches the paper here."}
    </p>
  );
}

function Skeleton() {
  return (
    <div className={styles.codeSkeleton} aria-hidden="true">
      <Shimmer width="70%" />
      <Shimmer width="30%" />
      <div className={styles.skeletonGap} />
      {[88, 64, 92, 40, 76, 58, 84, 48].map((w, i) => (
        <Shimmer key={i} width={`${w}%`} />
      ))}
    </div>
  );
}

function ConceptOnly({ onShowExample }: { onShowExample: () => void }) {
  return (
    <>
      <p className={styles.note}>
        This idea isn&apos;t spelled out in the code; the implementation relies on it without a line you
        could point to.
      </p>
      <Button onClick={onShowExample}>See an example</Button>
    </>
  );
}

function Grounded({ g, verdict, onShowExample }: { g: CodeGrounding; verdict: Verdict | null; onShowExample: () => void }) {
  if (g.grounding === "concept_only") return <ConceptOnly onShowExample={onShowExample} />;
  const blocks = buildBlocks(g);
  if (blocks.length === 0 && g.library_calls.length === 0) return <ConceptOnly onShowExample={onShowExample} />;
  return (
    <>
      {g.summary && <p className={styles.codeSummary}>{g.summary}</p>}
      {g.library_calls.length > 0 && (
        <p className={styles.libraries}>
          {blocks.length === 0 ? "Done by a library call: " : "Also calls: "}
          {g.library_calls.map((c) => (
            <code key={c} className={styles.libCall}>
              {c}
            </code>
          ))}
        </p>
      )}
      {blocks.map((b) => (
        <Block key={`${b.path}:${b.start}`} block={b} />
      ))}
      <VerdictBox verdict={verdict} />
    </>
  );
}

export function CodeTab({
  paperId,
  conceptId,
  hasRepo,
  verdict = null,
  onShowExample,
}: {
  paperId: string;
  conceptId: string;
  /** null while the paper is still loading */
  hasRepo: boolean | null;
  verdict?: Verdict | null;
  onShowExample: () => void;
}) {
  // No request until we know there is a repo to look in (free-tier budget).
  const q = useCode(paperId, hasRepo ? conceptId : null);
  if (hasRepo === false) return <p className={styles.note}>No code linked for this paper.</p>;
  if (q.error) {
    return (
      <div className={styles.error} role="alert">
        <p>{q.error instanceof ApiError ? q.error.message : "Couldn't load the code for this concept."}</p>
        <Button onClick={() => void q.refetch()}>Try again</Button>
      </div>
    );
  }
  if (!q.data) {
    return (
      <div aria-live="polite" aria-busy="true">
        <Skeleton />
      </div>
    );
  }
  return (
    <div aria-live="polite">
      <Grounded g={q.data} verdict={verdict} onShowExample={onShowExample} />
    </div>
  );
}
