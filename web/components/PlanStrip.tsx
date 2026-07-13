"use client";

import type { Snapshot } from "../lib/types";
import styles from "./PlanStrip.module.css";

export default function PlanStrip({ plan }: { plan: Snapshot["plan"] }) {
  if (!plan) {
    return (
      <section className={`card ${styles.strip}`}>
        <div className={styles.head}>
          <span className="eyebrow">plan</span>
        </div>
        <p className={styles.placeholder}>
          No plan yet — the coach publishes one as its first act.
        </p>
      </section>
    );
  }

  const marker = (status: string, index: number): string => {
    if (status === "done") return "✓";
    if (status === "skipped") return "—";
    return String(index + 1);
  };

  return (
    <section className={`card ${styles.strip} ${plan.stale ? styles.stale : ""}`}>
      <div className={styles.head}>
        <span className="eyebrow">plan · {plan.steps.length} steps</span>
        {plan.stale && <span className={styles.staleNote}>plan may be stale</span>}
      </div>
      <ol className={styles.steps}>
        {plan.steps.map((s, i) => (
          <li
            key={s.id}
            className={styles.step}
            data-status={s.status}
            data-current={plan.current_step_id === s.id || undefined}
            title={s.note ?? undefined}
          >
            <span className={styles.marker}>{marker(s.status, i)}</span>
            <span>
              {s.title}
              {s.note && <span className={styles.note}> — {s.note}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
