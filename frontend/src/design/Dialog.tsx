// A modal dialog (spec §13.12): labelled, focus moves in, Tab stays inside, Esc closes, focus
// returns to whatever opened it. The scrim is the only thing behind it that changes.

import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "./primitives";
import styles from "./dialog.module.css";

const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function Dialog({
  title,
  onClose,
  wide,
  initialFocus,
  children,
}: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  /** Focus this instead of the heading when the dialog opens (e.g. a search input). */
  initialFocus?: () => HTMLElement | null;
  children: ReactNode;
}) {
  const titleId = useId();
  const box = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (initialFocus?.() ?? heading.current)?.focus();
    return () => {
      if (opener && document.contains(opener)) opener.focus();
    };
    // Only on open/close: re-running on every render would steal focus from the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => el.getAttribute("aria-hidden") !== "true",
    );
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === heading.current)) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first?.focus();
    }
  }

  return (
    <div
      className={styles.scrim}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`${styles.dialog} ${wide ? styles.wide : ""}`}
        onKeyDown={onKeyDown}
      >
        <div className={styles.head}>
          <h2 id={titleId} ref={heading} tabIndex={-1} className={styles.title}>
            {title}
          </h2>
          <Button quiet aria-label={`Close ${title.toLowerCase()}`} onClick={onClose}>
            Close
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}
