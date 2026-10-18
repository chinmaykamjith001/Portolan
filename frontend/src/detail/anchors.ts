// Human labels for paper anchor ids (spec §9.1; id rules in docs/DECISIONS.md, M1).
// The backend knows the exact labels, but the drawer only gets ids, so derive them here.

const FLOATS: Record<string, string> = { eq: "Eq.", tab: "Table", fig: "Fig.", alg: "Alg." };

export const ANCHOR_ID = /^(?:abstract|(?:sec|para|eq|tab|fig|alg):[A-Za-z0-9.:~_-]+)$/;

function humanize(slug: string): string {
  const words = slug.replace(/-/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : slug;
}

/** "3.2" → "§3.2", "A" → "Appendix A", "front" → "Front matter", "3.notation" → "Notation". */
function scopeLabel(scope: string): string {
  if (scope === "front") return "Front matter";
  if (/^[A-Z]$/.test(scope)) return `Appendix ${scope}`;
  if (/^[0-9A-Z]+(?:\.[0-9]+)*$/.test(scope)) return `§${scope}`;
  const last = scope.split(".").pop() ?? scope;
  return humanize(last);
}

export function anchorLabel(id: string): string {
  const clean = id.replace(/~\d+$/, "");
  if (clean === "abstract") return "Abstract";
  const colon = clean.indexOf(":");
  if (colon < 0) return id;
  const kind = clean.slice(0, colon);
  const rest = clean.slice(colon + 1);
  if (!rest) return id;
  if (kind === "sec") return scopeLabel(rest);
  if (kind === "para") {
    const cut = rest.lastIndexOf(":");
    if (cut < 0) return id;
    return `${scopeLabel(rest.slice(0, cut))} ¶${rest.slice(cut + 1)}`;
  }
  const word = FLOATS[kind];
  if (word === undefined) return id;
  const unnumbered = /^(.*):u\d+$/.exec(rest);
  if (unnumbered) return `${word} in ${scopeLabel(unnumbered[1] ?? "")}`;
  return `${word} ${rest}`;
}
