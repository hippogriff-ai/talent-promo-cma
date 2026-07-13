"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import ChatBox from "../../../components/ChatBox";
import DraftPanel from "../../../components/DraftPanel";
import Feed from "../../../components/Feed";
import PlanStrip from "../../../components/PlanStrip";
import QuestionCard from "../../../components/QuestionCard";
import StatusBanner from "../../../components/StatusBanner";
import { api, ApiError } from "../../../lib/api";
import { connectRun, type ConnectionState } from "../../../lib/engineClient";
import type { ExportBundle, Snapshot } from "../../../lib/types";
import styles from "./run.module.css";

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [conn, setConn] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    const stream = connectRun(runId, {
      onSnapshot: (s) => {
        setSnap(s);
        setError(null);
      },
      onConnection: setConn,
      onError: (msg) => setError(msg),
    });
    return () => stream.close();
  }, [runId]);

  // resume_text comes from the export bundle (CONTRACT §7); fetched lazily, once
  const resumePromise = useRef<Promise<string | null> | null>(null);
  const getResumeText = useCallback(() => {
    resumePromise.current ??= api<ExportBundle>(`/api/coach/runs/${runId}/export`)
      .then((b) => b.run?.resume_text ?? null)
      .catch(() => {
        resumePromise.current = null; // allow retry
        return null;
      });
    return resumePromise.current;
  }, [runId]);

  const answer = useCallback(
    async (question_key: string, text: string) => {
      try {
        await api(`/api/coach/runs/${runId}/answers`, { body: { question_key, text } });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) return; // idempotent no-op
        throw e;
      }
    },
    [runId],
  );

  const skip = useCallback(
    async (question_key: string) => {
      try {
        await api(`/api/coach/runs/${runId}/answers`, { body: { question_key, skip: true } });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) return; // idempotent no-op
        throw e;
      }
    },
    [runId],
  );

  const sendMessage = useCallback(
    (text: string) => api<object>(`/api/coach/runs/${runId}/messages`, { body: { text } }).then(() => undefined),
    [runId],
  );

  const interrupt = useCallback(() => {
    void api(`/api/coach/runs/${runId}/interrupt`, { body: {} }).catch(() => undefined);
  }, [runId]);

  if (!snap) {
    return (
      <div>
        {error && <div className="notice notice-error">gateway unreachable: {error} — retrying…</div>}
        <div className={styles.loading}>Opening the run…</div>
      </div>
    );
  }

  return (
    <div>
      <StatusBanner snap={snap} conn={conn} onInterrupt={interrupt} />
      {error && <div className="notice notice-error">{error}</div>}
      <PlanStrip plan={snap.plan} />

      <div className={styles.layout}>
        <section className={styles.feedZone}>
          <div className={styles.zoneHead}>
            <span className="eyebrow">activity</span>
            <span className="eyebrow">{snap.feed.length} entries</span>
          </div>
          <Feed items={snap.feed} />
        </section>

        <aside className={styles.dock}>
          {snap.pending_questions.map((q) => (
            <QuestionCard
              key={q.question_key}
              q={q}
              onAnswer={(text) => answer(q.question_key, text)}
              onSkip={() => skip(q.question_key)}
            />
          ))}

          {snap.drafts.length > 0 && (
            <DraftPanel drafts={snap.drafts} verdicts={snap.verdicts} getResumeText={getResumeText} />
          )}

          {snap.pending_questions.length === 0 && snap.drafts.length === 0 && (
            <p className={styles.dockEmpty}>
              Questions and drafts land here. The coach will ask when it hits something only you
              can answer.
            </p>
          )}

          {snap.status !== "done" && snap.status !== "failed" && <div className="card"><ChatBox onSend={sendMessage} /></div>}
        </aside>
      </div>
    </div>
  );
}
