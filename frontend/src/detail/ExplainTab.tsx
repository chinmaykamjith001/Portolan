// Explain tab (spec §13.6): re-explain angles + the structured explanation with KaTeX math.
// A 202 from the backend means "being written": show reserved skeleton lines until SSE lands it.

import { useState } from "react";

import { ApiError } from "../api/client";
import { useExplanation } from "../api/hooks";
import type { Angle, Explanation } from "../api/types";
import { Button, Chip, Shimmer } from "../design/primitives";
import { anchorLabel } from "./anchors";
import styles from "./detail.module.css";
import { collectAnchors, InlineMarkdown, Markdown } from "./markdown";

export const ANGLES: { id: Angle; label: string }[] = [
  { id: "default", label: "Default" },
  { id: "simpler", label: "Simpler" },
  { id: "mathematical", label: "More mathematical" },
  { id: "analogy", label: "Analogy" },
  { id: "why", label: "Why it exists" },
  { id: "another_take", label: "Another take" },
];

const SECTIONS = [
  { key: "what_it_is", label: "What it is" },
  { key: "how_it_works", label: "How it works" },
  { key: "why_its_here", label: "Why it's here" },
  { key: "common_confusion", label: "Common confusion" },
] as const;

/** Anchors the explanation cites: the structured list first, then any [eq:1] refs in the text. */
export function citedAnchors(e: Explanation): string[] {
  const text = e.sections
    ? SECTIONS.map((s) => e.sections[s.key]).join("\n\n")
    : e.body_markdown;
  const ids = [...(e.sections?.where_it_appears ?? []), ...e.anchors_cited, ...collectAnchors(text)];
  return [...new Set(ids)];
}

function Skeleton() {
  return (
    <div className={styles.skeleton} aria-hidden="true">
      <Shimmer width="92%" />
      <Shimmer width="64%" />
      <div className={styles.skeletonGap} />
      <Shimmer width="38%" />
      <Shimmer />
      <Shimmer width="96%" />
      <Shimmer width="81%" />
      <div className={styles.skeletonGap} />
      <Shimmer width="42%" />
      <Shimmer />
      <Shimmer width="88%" />
      <Shimmer width="70%" />
    </div>
  );
}

function Body({ explanation }: { explanation: Explanation }) {
  const anchors = citedAnchors(explanation);
  const s = explanation.sections;
  return (
    <>
      <p className={styles.lead}>
        <InlineMarkdown text={explanation.gist} />
      </p>
      {s ? (
        SECTIONS.map(({ key, label }) =>
          s[key].trim() ? (
            <section key={key} aria-label={label}>
              <h3>{label}</h3>
              <Markdown source={s[key]} />
            </section>
          ) : null,
        )
      ) : (
        <Markdown source={explanation.body_markdown} />
      )}
      {anchors.length > 0 && (
        <section className={styles.where} aria-label="Where it appears">
          <h3 className={styles.whereLabel}>Where it appears</h3>
          <div className={styles.chips}>
            {anchors.map((id) => (
              <Chip key={id} title={id}>
                {anchorLabel(id)}
              </Chip>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

export function ExplainTab({ paperId, conceptId }: { paperId: string; conceptId: string }) {
  const [angle, setAngle] = useState<Angle>("default");
  const q = useExplanation(paperId, conceptId, angle);
  const ready = q.data !== undefined && q.data !== null;

  return (
    <>
      <ul className={styles.angles} aria-label="Explain it another way">
        {ANGLES.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              className={styles.angle}
              aria-pressed={a.id === angle}
              onClick={() => setAngle(a.id)}
            >
              {a.label}
            </button>
          </li>
        ))}
      </ul>
      {q.error ? (
        <div className={styles.error} role="alert">
          <p>{q.error instanceof ApiError ? q.error.message : "Couldn't load this explanation."}</p>
          <Button onClick={() => void q.refetch()}>Try again</Button>
        </div>
      ) : (
        <div className={styles.explanation} aria-live="polite" aria-busy={!ready}>
          {ready && q.data ? <Body explanation={q.data} /> : <Skeleton />}
        </div>
      )}
    </>
  );
}
