// A small Markdown renderer for explanations: paragraphs, ##/### headings, bullet lists, **bold**,
// *italic*, `code`, $inline$ and $$display$$ math (KaTeX, lazy-loaded) and [eq:1]-style anchor
// references. Text always goes through React (escaped); only KaTeX's own output is set as HTML.

import { useEffect, useState, type ReactNode } from "react";

import { ANCHOR_ID, anchorLabel } from "./anchors";
import styles from "./detail.module.css";

export type Inline =
  | { t: "text"; v: string }
  | { t: "code"; v: string }
  | { t: "math"; v: string; display: boolean }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "anchor"; ids: string[] };

export type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: 2 | 3; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "math"; tex: string };

// -- blocks ----------------------------------------------------------------------------------

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  let list: string[] | null = null;

  const flush = () => {
    if (para.length) out.push({ kind: "p", text: para.join(" ") });
    para = [];
    if (list) out.push({ kind: "ul", items: list });
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i] ?? "";
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    if (line.startsWith("$$")) {
      flush();
      const rest = line.slice(2);
      const close = rest.indexOf("$$");
      if (close >= 0) {
        out.push({ kind: "math", tex: rest.slice(0, close).trim() });
        const after = rest.slice(close + 2).trim();
        if (after) para.push(after);
        continue;
      }
      const tex = [rest];
      while (++i < lines.length) {
        const next = lines[i] ?? "";
        const end = next.indexOf("$$");
        if (end >= 0) {
          tex.push(next.slice(0, end));
          break;
        }
        tex.push(next);
      }
      out.push({ kind: "math", tex: tex.join("\n").trim() });
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      out.push({ kind: "h", level: (heading[1]?.length ?? 2) <= 2 ? 2 : 3, text: heading[2] ?? "" });
      continue;
    }
    const item = /^[-*+]\s+(.*)$/.exec(line);
    if (item) {
      if (para.length) {
        out.push({ kind: "p", text: para.join(" ") });
        para = [];
      }
      list = list ?? [];
      list.push(item[1] ?? "");
      continue;
    }
    if (list && /^\s/.test(raw)) {
      // indented continuation of the last bullet
      list[list.length - 1] = `${list[list.length - 1] ?? ""} ${line}`;
      continue;
    }
    if (list) flush();
    para.push(line);
  }
  flush();
  return out;
}

// -- inline ----------------------------------------------------------------------------------

const ESCAPABLE = "\\$*_`[]";

function anchorAt(s: string, i: number): { ids: string[]; end: number } | null {
  const m = /^\[([^\]\n]{1,200})\]/.exec(s.slice(i));
  if (!m) return null;
  const ids = (m[1] ?? "").split(/\s*[,;]\s*/).map((x) => x.trim());
  if (!ids.length || !ids.every((id) => ANCHOR_ID.test(id))) return null;
  return { ids, end: i + m[0].length };
}

/** `$x$` math needs a non-space after the opening and before the closing `$`, and no digit right
 * after the closing one, so "$5 and $10" stays text (the usual Pandoc rule). */
function inlineMathEnd(s: string, i: number): number {
  const first = s[i + 1];
  if (first === undefined || /\s/.test(first)) return -1;
  for (let j = i + 1; j < s.length; j++) {
    const ch = s[j];
    if (ch === "\\") {
      j++;
      continue;
    }
    if (ch === "$") {
      const before = s[j - 1] ?? "";
      const after = s[j + 1] ?? "";
      if (j > i + 1 && !/\s/.test(before) && !/[0-9]/.test(after)) return j;
      return -1;
    }
  }
  return -1;
}

function closing(s: string, from: number, marker: string, wordy: boolean): number {
  let j = from;
  while ((j = s.indexOf(marker, j)) >= 0) {
    const before = s[j - 1] ?? "";
    const after = s[j + marker.length] ?? "";
    if (j > from && !/\s/.test(before) && !(wordy && /[A-Za-z0-9]/.test(after))) return j;
    j += marker.length;
  }
  return -1;
}

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const push = (node: Inline) => {
    if (text) out.push({ t: "text", v: text });
    text = "";
    out.push(node);
  };

  let i = 0;
  while (i < s.length) {
    const ch = s[i] ?? "";
    const next = s[i + 1] ?? "";
    if (ch === "\\" && ESCAPABLE.includes(next) && next) {
      text += next;
      i += 2;
      continue;
    }
    if (ch === "`") {
      const end = s.indexOf("`", i + 1);
      if (end > i + 1) {
        push({ t: "code", v: s.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }
    if (ch === "$" && next === "$") {
      const end = s.indexOf("$$", i + 2);
      if (end > i + 2) {
        push({ t: "math", v: s.slice(i + 2, end).trim(), display: true });
        i = end + 2;
        continue;
      }
    } else if (ch === "$") {
      const end = inlineMathEnd(s, i);
      if (end > 0) {
        push({ t: "math", v: s.slice(i + 1, end), display: false });
        i = end + 1;
        continue;
      }
    }
    if (ch === "*" && next === "*" && !/\s/.test(s[i + 2] ?? " ")) {
      const end = closing(s, i + 2, "**", false);
      if (end > 0) {
        push({ t: "strong", c: parseInline(s.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }
    if ((ch === "*" || ch === "_") && next && !/\s/.test(next) && next !== ch) {
      const wordy = ch === "_";
      if (!(wordy && /[A-Za-z0-9]/.test(s[i - 1] ?? ""))) {
        const end = closing(s, i + 1, ch, wordy);
        if (end > 0) {
          push({ t: "em", c: parseInline(s.slice(i + 1, end)) });
          i = end + 1;
          continue;
        }
      }
    }
    if (ch === "[") {
      const ref = anchorAt(s, i);
      if (ref) {
        push({ t: "anchor", ids: ref.ids });
        i = ref.end;
        continue;
      }
    }
    text += ch;
    i++;
  }
  if (text) out.push({ t: "text", v: text });
  return out;
}

/** Anchor ids referenced as [eq:1] anywhere in the source, in order, without repeats. */
export function collectAnchors(source: string): string[] {
  const seen = new Set<string>();
  const walk = (nodes: Inline[]) => {
    for (const n of nodes) {
      if (n.t === "anchor") n.ids.forEach((id) => seen.add(id));
      else if (n.t === "strong" || n.t === "em") walk(n.c);
    }
  };
  for (const b of parseBlocks(source)) {
    if (b.kind === "p" || b.kind === "h") walk(parseInline(b.text));
    else if (b.kind === "ul") b.items.forEach((item) => walk(parseInline(item)));
  }
  return [...seen];
}

// -- KaTeX -----------------------------------------------------------------------------------

type RenderToString = (tex: string, options: { displayMode: boolean; throwOnError: boolean }) => string;

let katexRender: RenderToString | null = null;
let katexLoading: Promise<RenderToString> | null = null;

export function loadKatex(): Promise<RenderToString> {
  katexLoading ??= Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([m]) => {
    katexRender = m.renderToString;
    return m.renderToString;
  });
  return katexLoading;
}

function useKatex(): RenderToString | null {
  const [render, setRender] = useState<RenderToString | null>(() => katexRender);
  useEffect(() => {
    if (render) return;
    let live = true;
    loadKatex().then(
      (r) => live && setRender(() => r),
      () => undefined, // stay on the raw-TeX fallback
    );
    return () => {
      live = false;
    };
  }, [render]);
  return render;
}

export function MathTex({ tex, display }: { tex: string; display: boolean }) {
  const render = useKatex();
  const cls = display ? styles.mathDisplay : styles.math;
  if (!render) {
    return <code className={`${cls} ${styles.tex}`}>{tex}</code>;
  }
  let html: string;
  try {
    html = render(tex, { displayMode: display, throwOnError: false });
  } catch {
    return <code className={`${cls} ${styles.tex}`}>{tex}</code>;
  }
  return <span className={cls} dangerouslySetInnerHTML={{ __html: html }} />;
}

// -- rendering -------------------------------------------------------------------------------

function renderInline(nodes: Inline[], keyPrefix = ""): ReactNode[] {
  return nodes.map((n, i) => {
    const key = `${keyPrefix}${i}`;
    switch (n.t) {
      case "text":
        return n.v;
      case "code":
        return (
          <code key={key} className={styles.code}>
            {n.v}
          </code>
        );
      case "math":
        return <MathTex key={key} tex={n.v} display={n.display} />;
      case "strong":
        return <strong key={key}>{renderInline(n.c, `${key}.`)}</strong>;
      case "em":
        return <em key={key}>{renderInline(n.c, `${key}.`)}</em>;
      case "anchor":
        return (
          <span key={key} className={styles.ref}>
            {n.ids.map(anchorLabel).join(", ")}
          </span>
        );
    }
  });
}

export function InlineMarkdown({ text }: { text: string }) {
  return <>{renderInline(parseInline(text))}</>;
}

export function Markdown({ source }: { source: string }) {
  return (
    <>
      {parseBlocks(source).map((b, i) => {
        switch (b.kind) {
          case "p":
            return <p key={i}>{renderInline(parseInline(b.text))}</p>;
          case "h":
            return b.level === 2 ? (
              <h3 key={i}>{renderInline(parseInline(b.text))}</h3>
            ) : (
              <h4 key={i}>{renderInline(parseInline(b.text))}</h4>
            );
          case "ul":
            return (
              <ul key={i}>
                {b.items.map((item, j) => (
                  <li key={j}>{renderInline(parseInline(item))}</li>
                ))}
              </ul>
            );
          case "math":
            return (
              <div key={i} className={styles.mathBlock}>
                <MathTex tex={b.tex} display />
              </div>
            );
        }
      })}
    </>
  );
}
