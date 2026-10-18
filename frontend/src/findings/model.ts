// Pure helpers for findings (spec §10.9, §13.1, §13.6): ordering, wording, and what a concept's
// verdict is. No React here so the rules are easy to test.

import type { Finding, FindingsPayload, Severity, Verdict } from "../api/types";

/** Shown in the list without asking; everything else sits behind a quiet toggle (precision first). */
export const PROMINENT: readonly Verdict[] = ["mismatch", "undocumented_in_paper"];

export function isProminent(f: Finding): boolean {
  return PROMINENT.includes(f.verdict);
}

const SEVERITY_RANK: Record<Severity, number> = { high: 0, medium: 1, low: 2 };
const VERDICT_RANK: Record<Verdict, number> = {
  mismatch: 0,
  undocumented_in_paper: 1,
  uncertain: 2,
  not_found: 3,
  matches: 4,
};

/** Verdict first, then severity, then confidence (highest first); id keeps the order stable. */
export function sortFindings(list: readonly Finding[]): Finding[] {
  return [...list].sort(
    (a, b) =>
      VERDICT_RANK[a.verdict] - VERDICT_RANK[b.verdict] ||
      SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] ||
      b.confidence - a.confidence ||
      a.id.localeCompare(b.id),
  );
}

export function confidenceWords(confidence: number): string {
  if (confidence >= 0.85) return "high confidence";
  if (confidence >= 0.7) return "medium confidence";
  return "low confidence";
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  mismatch: "Mismatch",
  matches: "Matches paper",
  undocumented_in_paper: "Not in the paper",
  uncertain: "Uncertain",
  not_found: "Not found in the code",
};

export interface Summary {
  text: string;
  /** Mismatch gets the mismatch treatment (with its "!" cue); everything else stays neutral. */
  mismatch: boolean;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The paper strip's findings control. `null` means don't show one (no code, or not started). */
export function summarize(payload: FindingsPayload | null | undefined, hasRepo: boolean): Summary | null {
  if (!hasRepo || !payload || payload.state === "none") return null;
  const mismatches = payload.findings.filter((f) => f.verdict === "mismatch").length;
  const undocumented = payload.findings.filter((f) => f.verdict === "undocumented_in_paper").length;
  const found = [
    mismatches > 0 ? plural(mismatches, "mismatch", "mismatches") : "",
    undocumented > 0 ? `${undocumented} not in the paper` : "",
  ].filter(Boolean);
  if (payload.state === "running") {
    return {
      text: found.length > 0 ? `${found.join(" · ")} so far, still checking` : "Checking the code…",
      mismatch: mismatches > 0,
    };
  }
  if (payload.state === "failed") {
    return {
      text: found.length > 0 ? `${found.join(" · ")}. Couldn't finish checking` : "Couldn't finish checking",
      mismatch: mismatches > 0,
    };
  }
  return { text: found.length > 0 ? found.join(" · ") : "No mismatches found", mismatch: mismatches > 0 };
}

export type ConceptVerdictKind = "matches" | "mismatch" | "undocumented";

export interface ConceptVerdict {
  kind: ConceptVerdictKind;
  claim: string;
  evidence: string;
}

/**
 * What the drawer says about a concept: the strongest finding that names it. A mismatch beats
 * everything; "matches" only shows when nothing worse is attached. Uncertain results stay quiet
 * (they're listed in the Findings panel, not on the concept).
 */
export function conceptVerdict(findings: readonly Finding[] | undefined, conceptId: string): ConceptVerdict | null {
  const mine = sortFindings((findings ?? []).filter((f) => f.concept_ids.includes(conceptId)));
  const pick = (v: Verdict) => mine.find((f) => f.verdict === v);
  const hit = pick("mismatch") ?? pick("undocumented_in_paper") ?? pick("matches");
  if (!hit) return null;
  const kind: ConceptVerdictKind = hit.verdict === "undocumented_in_paper" ? "undocumented" : (hit.verdict as "matches" | "mismatch");
  return { kind, claim: hit.claim_text, evidence: hit.evidence };
}

/** Blob URL of a span in the repo at its commit, for GitHub and GitLab; null for anything else. */
export function spanHref(repoUrl: string | null | undefined, sha: string, path: string, start: number, end: number): string | null {
  if (!repoUrl || !/^[0-9a-f]{40}$/.test(sha)) return null;
  const base = repoUrl.replace(/\/$/, "").replace(/\.git$/, "");
  const lines = start === end ? `#L${start}` : `#L${start}-L${end}`;
  if (/^https:\/\/github\.com\//.test(base)) return `${base}/blob/${sha}/${path}${lines}`;
  if (/^https:\/\/gitlab\.com\//.test(base)) return `${base}/-/blob/${sha}/${path}${lines}`;
  return null;
}
