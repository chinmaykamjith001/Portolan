// Choose or upload the paper's code (spec §10.3, §13.1 "change" link). Changing it clears the
// old code notes and findings; indexing and checking restart in the background.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { ApiError, api, keys } from "../api/client";
import type { RepoCandidate } from "../api/types";
import { Dialog } from "../design/Dialog";
import { Button } from "../design/primitives";
import { useUI } from "../state/store";
import styles from "./dialogs.module.css";

function label(c: RepoCandidate): string {
  return c.host === "local" ? `${c.name} (your files)` : `${c.owner}/${c.name}`;
}

export function CodeDialog() {
  const close = useUI((s) => s.closeDialog);
  const paperId = useUI((s) => s.paperId);
  const qc = useQueryClient();
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const found = useQuery({
    queryKey: ["repoCandidates", paperId ?? ""],
    queryFn: async () => {
      const res = await api.repoCandidates(paperId ?? "");
      return res.pending ? null : res.data;
    },
    enabled: paperId !== null,
  });

  async function choose(choice: Parameters<typeof api.setRepo>[1]) {
    if (!paperId) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.setRepo(paperId, choice);
      // Code notes and findings belong to the old code: refetch everything that depends on it.
      for (const k of [
        keys.paper(paperId),
        ["code", paperId],
        keys.findings(paperId),
        ["layer", paperId],
      ]) {
        void qc.invalidateQueries({ queryKey: k });
      }
      close();
    } catch (err) {
      setMessage(
        err instanceof ApiError
          ? err.message
          : "Couldn't reach the Portolan server.",
      );
    } finally {
      setBusy(false);
    }
  }

  function useFolder(e: FormEvent) {
    e.preventDefault();
    if (folder.trim()) void choose({ local_path: folder.trim() });
  }

  const candidates = found.data?.candidates ?? [];
  return (
    <Dialog title="Code for this paper" onClose={close}>
      {found.data?.status && (
        <p className={styles.note}>{found.data.status.message}</p>
      )}
      {candidates.length > 0 && (
        <ul className={styles.choices}>
          {candidates.map((c) => (
            <li key={c.id}>
              <Button
                disabled={busy}
                onClick={() => void choose({ candidate_id: c.id })}
              >
                Use {label(c)}
              </Button>{" "}
              <span className={styles.note}>
                {c.official
                  ? "official"
                  : c.status === "ok"
                    ? "unofficial"
                    : c.status}
              </span>
            </li>
          ))}
        </ul>
      )}
      <label htmlFor="code-zip" className={styles.field}>
        Upload the code as a .zip
      </label>
      <input
        id="code-zip"
        type="file"
        accept=".zip,application/zip"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void choose(file);
        }}
      />
      <form onSubmit={useFolder}>
        <label htmlFor="code-folder" className={styles.field}>
          Or a folder on this computer
        </label>
        <input
          id="code-folder"
          className={styles.select}
          placeholder="C:\\Users\\you\\code\\project"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
        />{" "}
        <Button type="submit" disabled={busy || !folder.trim()}>
          Use folder
        </Button>
      </form>
      <p className={styles.note} role="alert">
        {message ?? ""}
      </p>
    </Dialog>
  );
}
