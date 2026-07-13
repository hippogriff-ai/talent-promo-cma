"use client";

import type { ConnectionState } from "../lib/engineClient";
import type { Snapshot } from "../lib/types";
import styles from "./StatusBanner.module.css";

const STATUS_LABEL: Record<Snapshot["status"], string> = {
  working: "working",
  needs_you: "needs you",
  done: "done",
  failed: "failed",
};

export default function StatusBanner({
  snap,
  conn,
  onInterrupt,
}: {
  snap: Snapshot;
  conn: ConnectionState;
  onInterrupt?: () => void;
}) {
  const { usage } = snap;
  return (
    <div className={styles.banner}>
      <span className={`status-pill status-${snap.status}`}>
        <span className="dot" />
        {STATUS_LABEL[snap.status]}
      </span>
      <span className={styles.title}>{snap.title || snap.run_id}</span>
      {conn !== "live" && conn !== "closed" && <span className={styles.conn}>{conn}…</span>}
      <span className={styles.spend}>
        {usage.total_tokens.toLocaleString()} tok
        {usage.usd != null && ` · $${usage.usd.toFixed(2)}`}
      </span>
      <span className={styles.engine}>{snap.engine}</span>
      {snap.status === "working" && onInterrupt && (
        <button className="btn btn-quiet" onClick={onInterrupt} title="Interrupt the run">
          interrupt
        </button>
      )}
    </div>
  );
}
