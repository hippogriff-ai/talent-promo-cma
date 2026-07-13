"use client";

import { useEffect, useMemo, useState } from "react";
import { lineDiff } from "../lib/diff";
import type { Draft, Verdict } from "../lib/types";
import styles from "./DraftPanel.module.css";

export default function DraftPanel({
  drafts,
  verdicts,
  getResumeText,
}: {
  drafts: Draft[];
  verdicts: Verdict[];
  /** lazily fetches the run's original resume_text (from the export bundle) */
  getResumeText: () => Promise<string | null>;
}) {
  const [picked, setPicked] = useState<string | null>(null); // draft_id; null → latest
  const [view, setView] = useState<"draft" | "diff">("draft");
  const [resume, setResume] = useState<string | null>(null);
  const [resumeErr, setResumeErr] = useState(false);

  const current = drafts.find((d) => d.draft_id === picked) ?? drafts[drafts.length - 1];

  useEffect(() => {
    if (view !== "diff" || resume !== null) return;
    let cancelled = false;
    getResumeText().then(
      (text) => {
        if (cancelled) return;
        if (text === null) setResumeErr(true);
        else setResume(text);
      },
      () => {
        if (!cancelled) setResumeErr(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [view, resume, getResumeText]);

  const diff = useMemo(
    () => (view === "diff" && resume !== null && current ? lineDiff(resume, current.draft) : null),
    [view, resume, current],
  );

  if (!current) return null;

  const verdict = [...verdicts].reverse().find((v) => v.draft_id === current.draft_id);

  const download = () => {
    const blob = new Blob([current.draft], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(current.label || current.draft_id).replace(/[^\w.-]+/g, "-")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={styles.panel}>
      <div className={styles.tabs}>
        {drafts.map((d, i) => (
          <button
            key={d.draft_id}
            className={`${styles.tab} ${d.draft_id === current.draft_id ? styles.tabActive : ""}`}
            onClick={() => setPicked(d.draft_id)}
          >
            {d.label || `v${i + 1}`}
          </button>
        ))}
      </div>

      <div className={`card ${styles.body}`}>
        {current.summary && <p className={styles.summary}>{current.summary}</p>}

        {verdict && (
          <div className={styles.verdict}>
            <div className={styles.verdictHead}>
              <span className="eyebrow">judge</span>
              <span className={styles.verdictResult} data-result={verdict.result}>
                {verdict.result === "satisfied" ? "satisfied" : "needs revision"}
              </span>
              <span className={styles.verdictIteration}>iteration {verdict.iteration}</span>
            </div>
            {verdict.explanation && (
              <p className={styles.verdictExplanation}>{verdict.explanation}</p>
            )}
            {verdict.findings.map((f, i) => (
              <div key={i} className={styles.finding} data-severity={f.severity}>
                <div className={styles.findingHead}>
                  <span className={styles.findingMode}>{f.failure_mode}</span>
                  <span className={styles.findingSeverity}>{f.severity}</span>
                </div>
                {f.span && <span className={styles.findingSpan}>“{f.span}”</span>}
                <p className={styles.findingRationale}>{f.rationale}</p>
              </div>
            ))}
            {verdict.rubric && (
              <table className={styles.rubric}>
                <thead>
                  <tr>
                    <th>dimension</th>
                    <th>score</th>
                    <th>rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(verdict.rubric).map(([dim, r]) => (
                    <tr key={dim}>
                      <td>{dim}</td>
                      <td className={styles.rubricScore}>{r.score}</td>
                      <td>{r.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        <div className={styles.viewRow}>
          <div className={styles.viewToggle}>
            <button
              className={`${styles.viewBtn} ${view === "draft" ? styles.viewBtnActive : ""}`}
              onClick={() => setView("draft")}
            >
              draft
            </button>
            <button
              className={`${styles.viewBtn} ${view === "diff" ? styles.viewBtnActive : ""}`}
              onClick={() => setView("diff")}
            >
              diff vs original
            </button>
          </div>
          <button className="btn btn-quiet" onClick={download}>
            download .md
          </button>
        </div>

        {view === "draft" && <pre className={styles.draftText}>{current.draft}</pre>}
        {view === "diff" && diff && (
          <pre className={styles.diffBox}>
            {diff.map((l, i) => (
              <span key={i} className={styles.diffLine} data-type={l.type}>
                {l.text}
                {"\n"}
              </span>
            ))}
          </pre>
        )}
        {view === "diff" && !diff && (
          <p className={styles.diffNote}>
            {resumeErr
              ? "couldn't load the original resume from the gateway — diff unavailable"
              : "loading the original resume…"}
          </p>
        )}
      </div>
    </div>
  );
}
