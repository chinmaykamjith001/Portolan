import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { ApiError, api } from "../api/client";
import { useBudget } from "../api/hooks";
import type { CreatePaperResult, TitleCandidate } from "../api/types";
import type { Maybe } from "../api/client";
import { Logo } from "../design/Logo";
import { Button, Popover } from "../design/primitives";
import { useUI } from "../state/store";
import styles from "./shell.module.css";

export type Theme = "system" | "light" | "dark";

function Budget() {
  const { data } = useBudget();
  if (!data || data.provider === "mock") return null;
  return (
    <span className={styles.budget} title="Free-tier requests used today (resets at midnight Pacific)">
      {data.requests_today.toLocaleString()} / ~{data.limits.rpd.toLocaleString()} requests today
    </span>
  );
}

export function TopBar({
  onCandidates,
  onError,
  theme,
  setTheme,
}: {
  onCandidates: (c: TitleCandidate[]) => void;
  onError: (message: string | null) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
}) {
  const setPaper = useUI((s) => s.setPaper);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<{ x: number; y: number } | null>(null);
  const { data: budget } = useBudget();

  const fileRef = useRef<HTMLInputElement>(null);

  async function run(call: () => Promise<Maybe<CreatePaperResult>>) {
    setBusy(true);
    onError(null);
    try {
      const res = await call();
      if (res.pending) return;
      if ("candidates" in res.data) onCandidates(res.data.candidates);
      else {
        onCandidates([]);
        setPaper(res.data.paper_id);
      }
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Couldn't reach the Portolan server. Is it running?");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const input = value.trim();
    if (input) void run(() => api.createPaper(input));
  }

  function upload(file: File | undefined) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      onError(`${file.name} doesn't look like a PDF. Upload a PDF file.`);
      return;
    }
    void run(() => api.uploadPdf(file));
  }

  function drop(e: DragEvent<HTMLInputElement>) {
    const file = e.dataTransfer.files[0];
    if (!file) return; // dropped text: let the input take it
    e.preventDefault();
    upload(file);
  }

  return (
    <header className={styles.topbar}>
      <button
        type="button"
        className={styles.brand}
        style={{ border: 0, background: "none", cursor: "pointer", padding: 0 }}
        onClick={() => setPaper(null)}
        aria-label="Portolan home"
      >
        <Logo />
        Portolan
      </button>
      <form className={styles.form} onSubmit={submit} role="search">
        <input
          className={styles.input}
          aria-label="Paper"
          placeholder="Paste an arXiv link, DOI, PDF or title"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("Files")) e.preventDefault();
          }}
          onDrop={drop}
        />
        <Button type="submit" disabled={busy}>
          {busy ? "Opening…" : "Open"}
        </Button>
        <Button quiet disabled={busy} onClick={() => fileRef.current?.click()}>
          Upload PDF
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          aria-label="PDF file"
          onChange={(e) => {
            upload(e.target.files?.[0]);
            e.target.value = ""; // choosing the same file again still fires
          }}
        />
      </form>
      <span className={styles.spacer} />
      <Budget />
      <Button
        quiet
        aria-label="Settings"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setSettings(settings ? null : { x: Math.max(16, r.right - 340), y: r.bottom + 8 });
        }}
      >
        Settings
      </Button>
      {settings && (
        <Popover x={settings.x} y={settings.y} onClose={() => setSettings(null)} label="Settings">
          <div className={styles.settings}>
            {budget?.provider === "gemini" && (
              <p>Free Gemini keys may let Google use what you send. Don&apos;t analyze private code with a free key.</p>
            )}
            <p>Provider: {budget?.provider ?? "unknown"}</p>
            <label htmlFor="theme">Theme</label>
            <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="system">Follow system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
        </Popover>
      )}
    </header>
  );
}
