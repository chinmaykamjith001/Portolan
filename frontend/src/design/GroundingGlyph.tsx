// Shape encodes grounding; color is reserved for state (spec §13.3). Used on Chart nodes,
// Route stops and in the drawer header, always at the same shapes.

import type { Grounding } from "../api/types";

export const GROUNDING_LABEL: Record<Grounding, string> = {
  concept_only: "Concept only",
  in_code: "In the code",
  in_library: "In a library",
  shown_by_running: "Shown by running",
  unknown: "Not grounded yet",
};

export function GroundingGlyph({ grounding, size = 12 }: { grounding: Grounding; size?: number }) {
  const s = size;
  const c = s / 2;
  const r = s / 2 - 1;
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.25 };
  let shape;
  switch (grounding) {
    case "in_code":
      shape = <rect x={1} y={1} width={s - 2} height={s - 2} rx={s * 0.22} {...common} />;
      break;
    case "in_library": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i + Math.PI / 6;
        return `${c + r * Math.cos(a)},${c + r * Math.sin(a)}`;
      }).join(" ");
      shape = <polygon points={pts} {...common} />;
      break;
    }
    case "shown_by_running":
      shape = <circle cx={c} cy={c} r={r} {...common} strokeDasharray="2.5 1.5" />;
      break;
    case "unknown":
      shape = <circle cx={c} cy={c} r={r} {...common} strokeDasharray="0.5 1.8" strokeLinecap="round" />;
      break;
    default:
      shape = <circle cx={c} cy={c} r={r} {...common} />;
  }
  return (
    <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} role="img" aria-label={GROUNDING_LABEL[grounding]}>
      {shape}
    </svg>
  );
}
