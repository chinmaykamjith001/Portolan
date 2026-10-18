// `/` (spec §13.10): jump to any concept the Chart has loaded so far. It searches the layers in
// the query cache only; it never asks the backend (and so never spends a request).

import { useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useRef, useState } from "react";

import type { Concept, LayerPayload } from "../api/types";
import { Dialog } from "../design/Dialog";
import { useUI } from "../state/store";
import styles from "./dialogs.module.css";

const MAX = 8;

/** Rank by where the query lands: title start, a word start in the title, the title, the gist. */
export function rankConcepts(concepts: Concept[], query: string): Concept[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const scored: [number, Concept][] = [];
  for (const c of concepts) {
    const title = c.title.toLowerCase();
    let score = 0;
    if (title.startsWith(q)) score = 4;
    else if (title.split(/[^a-z0-9]+/).some((w) => w.startsWith(q))) score = 3;
    else if (title.includes(q)) score = 2;
    else if (c.gist.toLowerCase().includes(q)) score = 1;
    if (score > 0) scored.push([score, c]);
  }
  scored.sort((a, b) => b[0] - a[0] || a[1].title.localeCompare(b[1].title));
  return scored.slice(0, MAX).map(([, c]) => c);
}

export function ConceptSearch() {
  const paperId = useUI((s) => s.paperId);
  const close = useUI((s) => s.closeDialog);
  const focusConcept = useUI((s) => s.focusConcept);
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const all = useMemo(() => {
    const byId = new Map<string, Concept>();
    for (const [, payload] of qc.getQueriesData<LayerPayload | null>({ queryKey: ["layer", paperId] })) {
      for (const c of Object.values(payload?.concepts ?? {})) byId.set(c.id, c);
    }
    return [...byId.values()];
  }, [qc, paperId]);
  const results = useMemo(() => rankConcepts(all, query), [all, query]);

  function pick(c: Concept | undefined) {
    if (!c) return;
    close();
    focusConcept(c.id, "chart");
  }

  return (
    <Dialog title="Search concepts" onClose={close} initialFocus={() => input.current}>
      <input
        ref={input}
        className={styles.search}
        role="combobox"
        aria-label="Search concepts"
        aria-expanded={results.length > 0}
        aria-controls={listId}
        aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
        aria-autocomplete="list"
        placeholder="Type a concept name"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && results.length > 0) {
            e.preventDefault();
            setActive((active + 1) % results.length);
          } else if (e.key === "ArrowUp" && results.length > 0) {
            e.preventDefault();
            setActive((active - 1 + results.length) % results.length);
          } else if (e.key === "Enter") {
            e.preventDefault();
            pick(results[active]);
          }
        }}
      />
      <ul id={listId} role="listbox" aria-label="Matching concepts" className={styles.results}>
        {results.map((c, i) => (
          <li key={c.id} role="presentation">
            <button
              type="button"
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              tabIndex={-1}
              className={styles.result}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(c)}
            >
              <span className={styles.resultTitle}>{c.title}</span>
              <span className={styles.resultGist}>{c.gist}</span>
            </button>
          </li>
        ))}
      </ul>
      {query.trim() && results.length === 0 && (
        <p className={styles.empty} role="status">
          {all.length === 0
            ? "The map is still loading."
            : "No loaded concept matches. Jump into a concept to load the ones below it."}
        </p>
      )}
    </Dialog>
  );
}
