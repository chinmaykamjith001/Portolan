// Example tab (spec §13.6, §10.7.3): a worked example whose numbers come from running a snippet.
// "Another example" asks for the next variant; the ones already seen stay one click away.

import { useState } from "react";

import { ApiError } from "../api/client";
import { useExample } from "../api/hooks";
import type { Example } from "../api/types";
import { Button, Shimmer } from "../design/primitives";
import styles from "./detail.module.css";

export const MAX_EXAMPLES = 8;

const SOURCE: Record<Example["source_label"], string> = {
  generic: "Computed by running a snippet",
  paper_notation: "Computed by running a snippet",
  from_repo_run: "From running the repo",
};

const NUMERIC = /^[-+]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?%?$/;

/** A column is numeric when every non-empty cell looks like a number. */
export function numericColumns(columns: string[], rows: string[][]): boolean[] {
  return columns.map((_c, i) => {
    const cells = rows.map((r) => (r[i] ?? "").trim()).filter((c) => c !== "");
    return cells.length > 0 && cells.every((c) => NUMERIC.test(c));
  });
}

function Skeleton() {
  return (
    <div className={styles.exampleSkeleton} aria-hidden="true">
      <Shimmer width="30%" />
      <Shimmer width="62%" />
      <div className={styles.skeletonGap} />
      {[0, 1, 2, 3].map((i) => (
        <Shimmer key={i} width={`${90 - i * 12}%`} />
      ))}
    </div>
  );
}

function ExampleBody({ example }: { example: Example }) {
  const numeric = numericColumns(example.columns, example.rows);
  return (
    <figure className={styles.example}>
      <p className={styles.source}>{SOURCE[example.source_label]}</p>
      <figcaption className={styles.exampleCaption}>{example.caption}</figcaption>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {example.columns.map((c, i) => (
                <th key={i} scope="col" className={numeric[i] ? styles.numCell : undefined}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {example.rows.map((row, r) => (
              <tr key={r}>
                {example.columns.map((_c, i) => (
                  <td key={i} className={numeric[i] ? `${styles.numCell} ${styles.mono}` : undefined}>
                    {row[i] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {example.note && <p className={styles.exampleNote}>{example.note}</p>}
      <details className={styles.snippet}>
        <summary>Show the snippet</summary>
        <pre>{example.program}</pre>
      </details>
    </figure>
  );
}

export function ExampleTab({ paperId, conceptId }: { paperId: string; conceptId: string }) {
  const [variant, setVariant] = useState(0);
  const [seen, setSeen] = useState(1); // how many variants the reader has asked for
  const q = useExample(paperId, conceptId, variant);

  const another = () => {
    const next = Math.min(seen, MAX_EXAMPLES - 1);
    setSeen(Math.max(seen, next + 1));
    setVariant(next);
  };

  return (
    <>
      <div aria-live="polite" aria-busy={!q.data && !q.error} className={styles.exampleArea}>
        {q.error ? (
          <div className={styles.error} role="alert">
            <p>{q.error instanceof ApiError ? q.error.message : "Couldn't load this example."}</p>
            <Button onClick={() => void q.refetch()}>Try again</Button>
          </div>
        ) : q.data ? (
          <ExampleBody example={q.data} />
        ) : (
          <Skeleton />
        )}
      </div>
      <div className={styles.exampleActions}>
        {seen > 1 && (
          <>
            <Button quiet disabled={variant === 0} onClick={() => setVariant(variant - 1)}>
              Previous
            </Button>
            <span className={styles.exampleCount}>
              Example {variant + 1} of {seen}
            </span>
            <Button quiet disabled={variant >= seen - 1} onClick={() => setVariant(variant + 1)}>
              Next
            </Button>
          </>
        )}
        <Button disabled={seen >= MAX_EXAMPLES || variant < seen - 1} onClick={another}>
          Another example
        </Button>
      </div>
    </>
  );
}
