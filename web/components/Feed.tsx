"use client";

import { useState } from "react";
import type { FeedItem } from "../lib/types";
import styles from "./Feed.module.css";

export default function Feed({ items }: { items: FeedItem[] }) {
  // seq → explicitly toggled open/closed; default comes from item.collapsed
  const [toggled, setToggled] = useState<Record<number, boolean>>({});

  if (items.length === 0) {
    return <div className={styles.empty}>Nothing yet — the coach is warming up.</div>;
  }

  return (
    <div className={styles.feed}>
      {items.map((item, i) => {
        const hasBody = item.body !== undefined && item.body !== "";
        const open = toggled[item.seq] ?? !item.collapsed;
        return (
          <article key={`${item.seq}-${i}`} className={styles.item} data-kind={item.kind}>
            <span className={styles.tag} data-kind={item.kind}>
              {item.kind}
            </span>
            <div className={styles.body}>
              <div className={styles.headline}>{item.headline}</div>
              {hasBody && (
                <>
                  <button
                    className={styles.expandBtn}
                    onClick={() => setToggled((t) => ({ ...t, [item.seq]: !open }))}
                  >
                    {open ? "collapse −" : "expand +"}
                  </button>
                  {open && <div className={styles.detail}>{item.body}</div>}
                </>
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
