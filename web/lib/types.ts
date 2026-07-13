// Mirrors CONTRACT.md §2 (HTTP API shapes) and §3 (WireEvent) exactly.
// When this file and CONTRACT.md disagree, CONTRACT.md wins.

export type Engine = "mock" | "cma";

export type RunStatus = "working" | "needs_you" | "done" | "failed";

// ── §2 HTTP API ──────────────────────────────────────────────────────────────

export type RunSummary = {
  run_id: string;
  engine: Engine;
  title: string;
  status: RunStatus;
  created_at: string;
  needs_you: boolean;
  spend_usd: number | null;
};

export type ApiErrorEnvelope = {
  type: "error";
  error: { type: string; message: string };
  request_id: string;
};

export type MemoryEntry = {
  id: string;
  path: string;
  size_bytes: number;
  updated_at: string;
};

export type MemoryList = { available: boolean; memories: MemoryEntry[] };

export type MemoryDoc = { id: string; path: string; content: string };

// ── §2 Snapshot (the fold's output) ──────────────────────────────────────────

export type PlanStep = {
  id: string;
  title: string;
  status: "pending" | "active" | "done" | "skipped";
  note?: string;
};

export type Question = {
  question_key: string;
  question: string;
  context?: string;
  kind?: "open" | "confirm" | "choice";
  options?: string[];
  asked_seq: number;
};

export type Draft = {
  draft_id: string;
  label: string;
  summary?: string;
  draft: string;
  seq: number;
};

export type JudgeFinding = {
  span: string;
  failure_mode: string;
  severity: "low" | "medium" | "high";
  rationale: string;
};

export type Verdict = {
  draft_id: string;
  result: "satisfied" | "needs_revision";
  explanation: string;
  iteration: number;
  findings: JudgeFinding[];
  rubric: Record<string, { score: number; rationale: string }> | null;
};

export type FeedItem = {
  seq: number;
  kind: "user" | "agent" | "tool" | "system" | "verdict" | "error";
  headline: string;
  body?: string;
  collapsed: boolean;
};

export type Usage = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  usd: number | null;
};

export type Snapshot = {
  run_id: string;
  engine: Engine;
  title: string;
  status: RunStatus;
  cursor: number; // last folded seq; resume SSE from here
  plan: { steps: PlanStep[]; current_step_id: string | null; stale: boolean } | null;
  feed: FeedItem[]; // display-ready, ordered
  pending_questions: Question[]; // CMA 0..n, mock 0..1
  drafts: Draft[]; // ordered by submission
  verdicts: Verdict[]; // parallel to drafts (draft_id)
  usage: Usage;
};

// ── §3 WireEvent ─────────────────────────────────────────────────────────────

export type WireEvent = {
  seq: number;
  id: string;
  type: string;
  processed_at: string | null;
  // ...payload (type-dependent; the fold reads what it needs)
  [key: string]: unknown;
};

// ── §7 Evidence bundle (only what the UI consumes: run.resume_text for diffs) ─

export type ExportBundle = {
  run: RunSummary & {
    resume_text: string;
    job_text: string | null;
    job_url: string | null;
    agent_ref: { engine: Engine; agent_id?: string; agent_version?: string; model?: string };
  };
  [key: string]: unknown;
};
