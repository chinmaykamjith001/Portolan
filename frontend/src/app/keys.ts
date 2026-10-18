// Global shortcuts (spec §13.10) that belong to no single view: `?` for help, `/` for concept
// search. View-specific keys (←/→, Tab, Enter, `f`) live with their views; Esc is in App.tsx.

import { useEffect } from "react";

import { useUI } from "../state/store";

export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
}

/** What a key does, given what's on screen. Pure so it can be tested without a DOM. */
export function shortcutFor(
  key: string,
  state: { paperId: string | null; view: string; dialog: string | null },
): "help" | "search" | null {
  if (state.dialog !== null) return null;
  if (key === "?") return "help";
  // On the FAQ the same key belongs to the ask box (FaqView), which has no paper-wide search.
  if (key === "/" && state.paperId !== null && state.view !== "faq") return "search";
  return null;
}

export function useGlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      const st = useUI.getState();
      const action = shortcutFor(e.key, st);
      if (action === null) return;
      e.preventDefault();
      st.openDialog(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
