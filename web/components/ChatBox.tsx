"use client";

import { useState } from "react";
import styles from "./ChatBox.module.css";

export default function ChatBox({ onSend }: { onSend: (text: string) => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await onSend(t);
      setText("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.box}>
      <span className="eyebrow">steer the coach</span>
      <textarea
        className="textarea"
        rows={2}
        placeholder="Say anything — messages queue while the coach works…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
        }}
        disabled={busy}
      />
      {err && <span className={styles.err}>{err}</span>}
      <div className={styles.row}>
        <button className="btn" onClick={() => void send()} disabled={busy || !text.trim()}>
          {busy ? "Sending…" : "Send"}
        </button>
        <span className={styles.hint}>⌘↩ to send</span>
      </div>
    </div>
  );
}
