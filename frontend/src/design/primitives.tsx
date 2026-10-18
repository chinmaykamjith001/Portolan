// Minimal primitives (spec §7 design/): Button, Tabs, Drawer, Popover, Shimmer, Chip.

import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import styles from "./primitives.module.css";

export function Button({
  quiet,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { quiet?: boolean }) {
  const cls = [styles.button, quiet ? styles.quiet : "", className ?? ""].join(" ").trim();
  return <button type="button" className={cls} {...props} />;
}

export interface TabItem<T extends string> {
  id: T;
  label: string;
  disabled?: boolean;
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className={styles.tabs}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={item.id === value}
          disabled={item.disabled}
          className={styles.tab}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function Drawer({
  open,
  wide,
  label,
  children,
}: {
  open: boolean;
  wide?: boolean;
  label: string;
  children: ReactNode;
}) {
  const cls = [styles.drawer, open ? styles.drawerOpen : "", wide ? styles.drawerWide : ""].join(" ");
  return (
    <aside className={cls} aria-label={label} aria-hidden={!open}>
      {children}
    </aside>
  );
}

/** A floating panel that closes on outside click. Esc is handled globally (spec §13.10). */
export function Popover({
  x,
  y,
  onClose,
  label,
  children,
}: {
  x: number;
  y: number;
  onClose: () => void;
  label: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [onClose]);
  return (
    <div ref={ref} role="dialog" aria-label={label} className={styles.popover} style={{ left: x, top: y }}>
      {children}
    </div>
  );
}

export function Shimmer({ width = "100%" }: { width?: string }) {
  return <span className={styles.shimmer} style={{ width }} aria-hidden="true" />;
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className={styles.chip} title={title}>
      {children}
    </span>
  );
}
