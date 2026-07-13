"use client";

import { useState } from "react";
import type { Question } from "../lib/types";
import styles from "./QuestionCard.module.css";

export default function QuestionCard({
  q,
  onAnswer,
  onSkip,
}: {
  q: Question;
  onAnswer: (text: string) => Promise<void>;
  onSkip: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (answer: string) => {
    if (!answer.trim() || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onAnswer(answer.trim());
      setSent(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const skip = async () => {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onSkip();
      setSent(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className={`card ${styles.cardEl}`}>
        <span className="eyebrow">the coach asks</span>
        <div className={styles.question}>{q.question}</div>
        <span className={styles.sent}>Answer sent — the coach is back to work.</span>
      </div>
    );
  }

  const isConfirm = q.kind === "confirm";
  const options = q.options ?? (isConfirm ? ["Yes", "No"] : undefined);

  return (
    <div className={`card ${styles.cardEl}`}>
      <div className={styles.eyebrowRow}>
        <span className="eyebrow">the coach asks</span>
        {q.kind && <span className={styles.kind}>{q.kind}</span>}
      </div>
      <div className={styles.question}>{q.question}</div>
      {q.context && <p className={styles.context}>{q.context}</p>}

      {options && (
        <div className={styles.options}>
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={`${styles.option} ${selected === opt ? styles.optionSelected : ""}`}
              onClick={() => setSelected(selected === opt ? null : opt)}
              disabled={busy}
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      <div className={styles.answerRow}>
        <textarea
          className="textarea"
          rows={3}
          placeholder={options ? "…or answer in your own words" : "Your answer…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
        {err && <span className={styles.err}>{err}</span>}
        <div className={styles.actions}>
          <button
            className="btn btn-primary"
            disabled={busy || (!text.trim() && !selected)}
            onClick={() => void submit(text.trim() || selected || "")}
          >
            {busy ? "Sending…" : "Answer"}
          </button>
          <button className="btn btn-quiet" disabled={busy} onClick={() => void skip()}>
            I don&rsquo;t know / skip
          </button>
        </div>
      </div>
    </div>
  );
}
