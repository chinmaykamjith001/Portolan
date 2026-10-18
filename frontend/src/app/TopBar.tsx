import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { ApiError, api } from "../api/client";
import { useBudget } from "../api/hooks";
import type { CreatePaperResult, TitleCandidate } from "../api/types";
import type { Maybe } from "../api/client";
import { Logo } from "../design/Logo";
import { Button } from "../design/primitives";
import { useUI } from "../state/store";
import { SERVER_DOWN } from "./messages";
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
}: {
  onCandidates: (c: TitleCandidate[]) => void;
  onError: (message: string | null) => void;
}) {
  const setPaper = useUI((s) => s.setPaper);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const openDialog = useUI((s) => s.openDialog);

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
      onError(err instanceof ApiError ? err.message : SERVER_DOWN);
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
      <Button quiet onClick={() => openDialog("settings")}>
        Settings
      </Button>
    </header>
  );
}
