"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Engine, RunSummary } from "../lib/types";
import styles from "./intake.module.css";

const STATUS_LABEL: Record<string, string> = {
  working: "working",
  needs_you: "needs you",
  done: "done",
  failed: "failed",
};

export default function IntakePage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [resumeText, setResumeText] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [jobText, setJobText] = useState("");
  const [engine, setEngine] = useState<Engine>("mock");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      const res = await api<{ runs: RunSummary[] }>("/api/coach/runs");
      setRuns(res.runs);
      setListError(null);
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void loadRuns();
    const t = setInterval(() => void loadRuns(), 8000);
    return () => clearInterval(t);
  }, [loadRuns]);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!resumeText.trim()) {
      setError("Paste your current resume — the coach grounds every claim in it.");
      return;
    }
    if (!jobUrl.trim() && !jobText.trim()) {
      setError("Give the coach a job: a URL or the pasted posting (either works).");
      return;
    }
    setStarting(true);
    try {
      const body: Record<string, unknown> = { engine, resume_text: resumeText };
      if (title.trim()) body.title = title.trim();
      if (jobUrl.trim()) body.job_url = jobUrl.trim();
      if (jobText.trim()) body.job_text = jobText;
      const res = await api<{ run_id: string }>("/api/coach/runs", { body });
      router.push(`/run/${res.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <section className={styles.lede}>
        <h1>Bring a resume. Leave with the one you didn&rsquo;t know you had.</h1>
        <p>
          The coach researches the job, interviews you for the experience you never wrote down, and
          drafts a resume where every claim is grounded.
        </p>
      </section>

      <form className={`card ${styles.form}`} onSubmit={start}>
        <div className={styles.field}>
          <label htmlFor="title">Title</label>
          <input
            id="title"
            className="input"
            placeholder="Acme — Staff Engineer (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="resume">Your current resume</label>
          <textarea
            id="resume"
            className="textarea"
            rows={10}
            placeholder="Paste the full text of your resume…"
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="jobUrl">The job</label>
          <div className={styles.jobGrid}>
            <input
              id="jobUrl"
              className="input"
              placeholder="https://… (posting URL)"
              value={jobUrl}
              onChange={(e) => setJobUrl(e.target.value)}
            />
            <div className={styles.or}>or paste the posting</div>
            <textarea
              className="textarea"
              rows={6}
              placeholder="Paste the job description…"
              value={jobText}
              onChange={(e) => setJobText(e.target.value)}
            />
          </div>
          <p className={styles.hint}>Provide at least one — URL or pasted text.</p>
        </div>

        {error && <div className="notice notice-error">{error}</div>}

        <div className={styles.actions}>
          <select
            className={`select ${styles.engineSelect}`}
            value={engine}
            onChange={(e) => setEngine(e.target.value as Engine)}
            aria-label="Engine"
          >
            <option value="mock">mock — no keys</option>
            <option value="cma">cma — live agent</option>
          </select>
          <button className="btn btn-primary" type="submit" disabled={starting}>
            {starting ? "Starting…" : "Start coaching"}
          </button>
        </div>
      </form>

      <section className={styles.runList}>
        <div className={styles.runListHead}>
          <h2 style={{ fontSize: "1.1rem" }}>Runs</h2>
          <span className="eyebrow">newest first</span>
        </div>
        {listError && <div className="notice notice-error">gateway unreachable: {listError}</div>}
        {runs && runs.length === 0 && !listError && (
          <div className={styles.empty}>No runs yet — start one above.</div>
        )}
        {runs?.map((r) => (
          <Link key={r.run_id} href={`/run/${r.run_id}`} className={styles.runRow}>
            <span className={styles.runTitle}>{r.title || r.run_id}</span>
            {r.needs_you && <span className="needs-you-badge">needs you</span>}
            <span className={`status-pill status-${r.status}`}>
              <span className="dot" />
              {STATUS_LABEL[r.status] ?? r.status}
            </span>
            <span className={styles.engineTag}>{r.engine}</span>
            <span className={styles.runMeta}>
              {r.spend_usd != null ? `$${r.spend_usd.toFixed(2)} · ` : ""}
              {formatWhen(r.created_at)}
            </span>
          </Link>
        ))}
      </section>
    </div>
  );
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
