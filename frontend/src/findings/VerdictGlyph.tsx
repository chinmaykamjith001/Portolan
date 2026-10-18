// Shape cues for a verdict (spec §13.12): never colour alone. Only a mismatch is drawn in the
// mismatch colour; the rest stay in ink.

import type { Verdict } from "../api/types";

export function VerdictGlyph({ verdict, size = 14 }: { verdict: Verdict; size?: number }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const };
  let mark;
  switch (verdict) {
    case "mismatch":
      mark = (
        <>
          <path d="M7 3.5 V8" {...common} />
          <circle cx="7" cy="10.5" r="0.4" {...common} />
        </>
      );
      break;
    case "matches":
      mark = <path d="M4 7.2 L6.2 9.4 L10 4.8" {...common} strokeLinejoin="round" />;
      break;
    case "undocumented_in_paper":
      mark = <path d="M7 4 V10 M4 7 H10" {...common} />;
      break;
    case "uncertain":
      mark = (
        <>
          <path d="M5.2 5.4 Q5.4 3.8 7 3.8 Q8.8 3.8 8.8 5.4 Q8.8 6.4 7 7.2 V8" {...common} />
          <circle cx="7" cy="10.4" r="0.4" {...common} />
        </>
      );
      break;
    default:
      mark = <path d="M4.5 7 H9.5" {...common} />;
  }
  return (
    <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      <circle cx="7" cy="7" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.1" />
      {mark}
    </svg>
  );
}
