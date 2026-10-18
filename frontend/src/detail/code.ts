// View model for the Code tab: turns a CodeGrounding into blocks of numbered lines, with each
// note attached to its line so the callout renders directly beneath it (spec §13.6).

import type { CodeGrounding, LineNote } from "../api/types";
import { languageOf, type Lang } from "./highlight";

export interface CodeLine {
  n: number;
  text: string;
  notes: LineNote[];
}

export interface CodeBlock {
  path: string;
  start: number;
  end: number;
  lang: Lang;
  /** The file at the commit, anchored to these lines. */
  href: string | null;
  lines: CodeLine[];
  hasNotes: boolean;
}

export const KIND_LABEL: Record<LineNote["kind"], string> = {
  implements: "Implements",
  detail_not_in_paper: "Not in the paper",
  mismatch: "Mismatch",
  context: "Context",
};

/** "Lines 41–45" / "Line 41". */
export function lineRange(start: number, end: number): string {
  return end > start ? `Lines ${start}–${end}` : `Line ${start}`;
}

export function buildBlocks(g: CodeGrounding): CodeBlock[] {
  const placed = new Set<number>(); // a note shows once even if two spans overlap it
  const blocks: CodeBlock[] = [];
  for (const span of g.spans) {
    const file = g.code[span.path] ?? {};
    const lines: CodeLine[] = [];
    for (let n = span.start_line; n <= span.end_line; n++) {
      const text = file[String(n)];
      if (text === undefined) continue;
      const notes = g.notes.filter((note, i) => {
        if (note.line !== n || placed.has(i)) return false;
        placed.add(i);
        return true;
      });
      lines.push({ n, text, notes });
    }
    if (lines.length === 0) continue;
    const base = g.links[span.path];
    blocks.push({
      path: span.path,
      start: span.start_line,
      end: span.end_line,
      lang: languageOf(span.path),
      href: base ? `${base}#L${span.start_line}-L${span.end_line}` : null,
      lines,
      hasNotes: lines.some((l) => l.notes.length > 0),
    });
  }
  return blocks;
}
