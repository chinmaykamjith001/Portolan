// The FAQ view (spec §13.8): a single column on the canvas background. Ask box, source filters,
// then questions with answers, a source label, citations and links back to the Chart and Route.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { useFaq, useLayer } from "../api/hooks";
import type { FAQItem, FaqSource, LayerPayload } from "../api/types";
import { Button, Shimmer } from "../design/primitives";
import { useUI } from "../state/store";
import { AskBox } from "./AskBox";
import { FaqItemView } from "./FaqItemView";
import { filterItems, kindCounts, mergeItems } from "./faq";
import styles from "./faq.module.css";

function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

function Loading() {
  return (
    <div className={styles.loading} role="status" aria-live="polite">
      <p>Gathering questions from reviews, issues and the paper…</p>
      {[0, 1, 2].map((i) => (
        <div key={i} className={styles.skeleton} aria-hidden="true">
          <Shimmer width="70%" />
          <Shimmer />
          <Shimmer width="88%" />
        </div>
      ))}
    </div>
  );
}

export default function FaqView() {
  const paperId = useUI((s) => s.paperId);
  const qc = useQueryClient();
  const { data, isError, isFetching, refetch } = useFaq(paperId);
  useLayer(paperId, "master"); // so "Show on Chart" can name concepts even if the Chart never opened
  const [asked, setAsked] = useState<FAQItem[]>([]);
  const [kind, setKind] = useState<FaqSource | "all">("all");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || useUI.getState().dialog) return;
      e.preventDefault();
      input.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const titles = new Map<string, string>();
  if (paperId) {
    for (const [, payload] of qc.getQueriesData<LayerPayload | null>({ queryKey: ["layer", paperId] })) {
      for (const c of Object.values(payload?.concepts ?? {})) titles.set(c.id, c.title);
    }
  }
  const titleOf = (id: string) => titles.get(id);

  const stored = data?.items;
  const items = useMemo(() => mergeItems(asked, stored ?? []), [asked, stored]);
  const kinds = kindCounts(items);
  const active = kinds.some((k) => k.kind === kind) ? kind : "all";
  const shown = filterItems(items, active);
  const freshIds = new Set(asked.map((i) => i.id));

  if (paperId === null) return null;

  let body;
  if (isError) {
    body = (
      <div className={styles.state} role="alert">
        <p>Couldn&apos;t load the questions for this paper. Try again in a moment.</p>
        <Button onClick={() => void refetch()} disabled={isFetching}>
          Try again
        </Button>
      </div>
    );
  } else if (stored === undefined || stored === null) {
    body = asked.length > 0 ? null : <Loading />;
  } else if (items.length === 0) {
    body = (
      <p className={styles.state} role="status">
        No questions yet. Ask one above.
      </p>
    );
  } else {
    body = null;
  }

  return (
    <section className={styles.root} aria-label="Questions about this paper">
      <div className={styles.column}>
        <AskBox ref={input} paperId={paperId} onAnswer={(item) => setAsked((a) => [item, ...a])} />
        {kinds.length > 0 && (
          <div className={styles.filters} role="group" aria-label="Filter by source">
            <button type="button" className={styles.filter} aria-pressed={active === "all"} onClick={() => setKind("all")}>
              All {items.length}
            </button>
            {kinds.map((k) => (
              <button
                key={k.kind}
                type="button"
                className={styles.filter}
                aria-pressed={active === k.kind}
                onClick={() => setKind(k.kind)}
              >
                {k.label} {k.count}
              </button>
            ))}
          </div>
        )}
        {body}
        {shown.length > 0 && (
          <div className={styles.list}>
            {shown.map((item) => (
              <FaqItemView key={item.id} item={item} titleOf={titleOf} fresh={freshIds.has(item.id)} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
