// The findings count in the paper strip (spec §13.1): "1 mismatch", "Checking the code…", …

import { useFindings } from "../api/hooks";
import type { PaperInfo } from "../api/types";
import { useUI } from "../state/store";
import styles from "./findings.module.css";
import { summarize } from "./model";
import { VerdictGlyph } from "./VerdictGlyph";

export function FindingsControl({ paper }: { paper: PaperInfo | null | undefined }) {
  const paperId = useUI((s) => s.paperId);
  const open = useUI((s) => s.findingsOpen);
  const setOpen = useUI((s) => s.setFindingsOpen);
  const hasRepo = Boolean(paper?.repo);
  const q = useFindings(hasRepo ? paperId : null);
  const summary = summarize(q.data, hasRepo);
  if (!summary) return null;
  return (
    <button
      type="button"
      className={`${styles.control} ${summary.mismatch ? styles.controlMismatch : ""}`}
      aria-expanded={open}
      aria-label={`Findings: ${summary.text}`}
      onClick={() => setOpen(!open)}
    >
      {summary.mismatch && <VerdictGlyph verdict="mismatch" size={12} />}
      {summary.text}
    </button>
  );
}
