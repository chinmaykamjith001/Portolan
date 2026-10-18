import { useMutation } from "@tanstack/react-query";
import { forwardRef, useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "../api/client";
import type { FAQItem } from "../api/types";
import { Button } from "../design/primitives";
import styles from "./faq.module.css";

const SLOW_AFTER_MS = 8000;

/** Ask box (spec §10.10): one P0 question; the answer arrives as a FAQItem. */
export const AskBox = forwardRef<HTMLInputElement, { paperId: string; onAnswer: (item: FAQItem) => void }>(
  function AskBox({ paperId, onAnswer }, ref) {
    const [question, setQuestion] = useState("");
    const [slow, setSlow] = useState(false);
    const ask = useMutation({
      mutationFn: async (q: string) => {
        const res = await api.ask(paperId, q);
        if (res.pending) throw new ApiError(202, "pending", "The answer isn't ready yet. Try again.");
        return res.data;
      },
      onSuccess: (item) => {
        setQuestion("");
        onAnswer(item);
      },
    });

    // Free-tier waits are normal; say so once instead of showing an error.
    useEffect(() => {
      if (!ask.isPending) {
        setSlow(false);
        return;
      }
      const t = window.setTimeout(() => setSlow(true), SLOW_AFTER_MS);
      return () => window.clearTimeout(t);
    }, [ask.isPending]);

    function submit(e: FormEvent) {
      e.preventDefault();
      const q = question.trim();
      if (q && !ask.isPending) ask.mutate(q);
    }

    const message = ask.isError
      ? ask.error instanceof ApiError
        ? ask.error.message
        : "Couldn't reach the Portolan server. Is it running?"
      : null;

    return (
      <form className={styles.ask} onSubmit={submit} role="search" aria-label="Ask about this paper">
        <input
          ref={ref}
          className={styles.askInput}
          aria-label="Ask about this paper"
          placeholder="Ask about this paper"
          maxLength={500}
          value={question}
          disabled={ask.isPending}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <Button type="submit" disabled={ask.isPending || question.trim() === ""}>
          {ask.isPending ? "Asking…" : "Ask"}
        </Button>
        <p className={styles.askStatus} aria-live="polite">
          {ask.isPending
            ? slow
              ? "Reading the paper and code… The free tier can make this take a little longer."
              : "Reading the paper and code…"
            : (message ?? "")}
        </p>
      </form>
    );
  },
);
