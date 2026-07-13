"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { MemoryDoc, MemoryList } from "../../lib/types";
import styles from "./memory.module.css";

export default function MemoryPage() {
  const [list, setList] = useState<MemoryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [doc, setDoc] = useState<MemoryDoc | null>(null);
  const [docLoading, setDocLoading] = useState(false);

  useEffect(() => {
    api<MemoryList>("/api/coach/memory").then(setList, (e) =>
      setError(e instanceof Error ? e.message : String(e)),
    );
  }, []);

  useEffect(() => {
    if (!selected) return;
    setDocLoading(true);
    setDoc(null);
    api<MemoryDoc>(`/api/coach/memory/${selected}`)
      .then(setDoc, () => setDoc(null))
      .finally(() => setDocLoading(false));
  }, [selected]);

  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <h1>What the coach knows about you</h1>
        <p>Claims, preferences, and lessons the coach has written to memory across runs.</p>
      </div>

      {error && <div className="notice notice-error">gateway unreachable: {error}</div>}

      {!error && !list && <div className={`card ${styles.stateBox}`}>Reading memory…</div>}

      {list && !list.available && (
        <div className={`card ${styles.stateBox}`}>
          Memory isn&rsquo;t available on this engine — mock runs don&rsquo;t learn. Start a CMA
          run to give the coach a memory.
        </div>
      )}

      {list && list.available && list.memories.length === 0 && (
        <div className={`card ${styles.stateBox}`}>
          The coach hasn&rsquo;t learned anything yet — start a run.
        </div>
      )}

      {list && list.available && list.memories.length > 0 && (
        <div className={styles.grid}>
          <div className={`card ${styles.list}`}>
            {list.memories.map((m) => (
              <button
                key={m.id}
                className={`${styles.entry} ${selected === m.id ? styles.entryActive : ""}`}
                onClick={() => setSelected(m.id)}
              >
                <span className={styles.entryPath}>{m.path}</span>
                <span className={styles.entryMeta}>
                  {formatBytes(m.size_bytes)} · {formatWhen(m.updated_at)}
                </span>
              </button>
            ))}
          </div>
          <div className={`card ${styles.viewer}`}>
            {!selected && <div className={styles.stateBox}>Pick a memory to read it.</div>}
            {selected && docLoading && <div className={styles.stateBox}>Loading…</div>}
            {selected && !docLoading && doc && (
              <>
                <div className={styles.viewerPath}>{doc.path}</div>
                <pre className={styles.content}>{doc.content}</pre>
              </>
            )}
            {selected && !docLoading && !doc && (
              <div className={styles.stateBox}>Couldn&rsquo;t load that memory.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
