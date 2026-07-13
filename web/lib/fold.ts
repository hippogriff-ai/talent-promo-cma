// Pure TS event fold — behaviorally identical to the Python fold in the gateway.
// Rules come from CONTRACT.md §3 (fold table + staleness rule). The golden test
// (test/fold.golden.test.ts) pins both folds to the same fixture snapshot.

import type {
  Draft,
  Engine,
  FeedItem,
  JudgeFinding,
  PlanStep,
  Question,
  RunStatus,
  Snapshot,
  Usage,
  Verdict,
  WireEvent,
} from "./types";

export type RunMeta = { run_id: string; engine: Engine; title: string };

/** Tool names that render as plan/question/draft regardless of tool_use event flavor. */
const CUSTOM_TOOL_NAMES = new Set(["update_plan", "ask_user", "submit_draft"]);

/** CONTRACT §3: stale when > 15 countable events since last update_plan while a step is active. */
const STALE_THRESHOLD = 15;

/** Max length of the "{name}: {short input}" input rendering in tool feed headlines. */
const SHORT_INPUT_MAX = 80;

export type FoldState = {
  meta: RunMeta;
  status: RunStatus;
  cursor: number;
  planSteps: PlanStep[] | null;
  currentStepId: string | null;
  feed: FeedItem[];
  pending: Question[];
  drafts: Draft[];
  verdicts: Verdict[];
  usage: Usage;
  /** countable events (agent.message / non-custom tool_use / span.*) since last update_plan */
  sincePlan: number;
};

export function initialState(meta: RunMeta): FoldState {
  return {
    meta,
    status: "working",
    cursor: 0,
    planSteps: null,
    currentStepId: null,
    feed: [],
    pending: [],
    drafts: [],
    verdicts: [],
    usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, usd: null },
    sincePlan: 0,
  };
}

// ── helpers ──────────────────────────────────────────────────────────────────

/** Extract plain text from MA content blocks (`[{type:"text", text}]`) or a bare string. */
function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter(
        (b): b is { type: string; text: string } =>
          !!b && typeof b === "object" && (b as { type?: unknown }).type === "text" &&
          typeof (b as { text?: unknown }).text === "string",
      )
      .map((b) => b.text)
      .join("\n");
  }
  return "";
}

/** headline = first line, body = rest (omitted when blank). */
function splitHeadline(text: string): { headline: string; body?: string } {
  const i = text.indexOf("\n");
  if (i === -1) return { headline: text };
  const body = text.slice(i + 1);
  return body.trim() ? { headline: text.slice(0, i), body } : { headline: text.slice(0, i) };
}

function shortInput(input: unknown): string {
  let s: string;
  try {
    s = JSON.stringify(input) ?? "";
  } catch {
    s = String(input);
  }
  return s.length > SHORT_INPUT_MAX ? s.slice(0, SHORT_INPUT_MAX - 1) + "…" : s;
}

function pushText(state: FoldState, seq: number, kind: FeedItem["kind"], text: string, collapsed: boolean): void {
  const { headline, body } = splitHeadline(text);
  const item: FeedItem = { seq, kind, headline, collapsed };
  if (body !== undefined) item.body = body;
  state.feed.push(item);
}

// ── custom tools (update_plan / ask_user / submit_draft) ─────────────────────

function applyCustomTool(
  state: FoldState,
  ev: WireEvent,
  name: string,
  input: Record<string, unknown>,
  toolUseId: string | undefined,
): void {
  if (name === "update_plan") {
    const rawSteps = Array.isArray(input.steps) ? (input.steps as Record<string, unknown>[]) : [];
    state.planSteps = rawSteps.map((s) => {
      const step: PlanStep = {
        id: String(s.id ?? ""),
        title: String(s.title ?? ""),
        status: (s.status as PlanStep["status"]) ?? "pending",
      };
      if (s.note != null) step.note = String(s.note);
      return step;
    });
    state.currentStepId = input.current_step_id != null ? String(input.current_step_id) : null;
    state.sincePlan = 0; // reset staleness counter
  } else if (name === "ask_user") {
    const key = toolUseId ?? ev.id;
    const q: Question = {
      question_key: key,
      question: String(input.question ?? ""),
      asked_seq: ev.seq,
    };
    if (input.context != null) q.context = String(input.context);
    if (input.kind != null) q.kind = input.kind as Question["kind"];
    if (Array.isArray(input.options)) q.options = (input.options as unknown[]).map(String);
    state.pending.push(q);
  } else if (name === "submit_draft") {
    const d: Draft = {
      draft_id: toolUseId ?? ev.id,
      label: input.label != null ? String(input.label) : "",
      draft: String(input.draft ?? input.text ?? ""), // CONTRACT: accept input.text fallback
      seq: ev.seq,
    };
    if (input.summary != null) d.summary = String(input.summary);
    state.drafts.push(d);
  }
  // unknown custom tool names: ignored
}

// ── the fold ─────────────────────────────────────────────────────────────────

export function applyEvent(state: FoldState, ev: WireEvent): void {
  if (typeof ev.seq === "number") state.cursor = Math.max(state.cursor, ev.seq);
  const input = (ev.input ?? {}) as Record<string, unknown>;
  const toolUseId = typeof ev.tool_use_id === "string" ? ev.tool_use_id : undefined;

  switch (ev.type) {
    case "user.message":
      pushText(state, ev.seq, "user", textFromContent(ev.content), false);
      break;

    case "agent.message":
      state.sincePlan++;
      pushText(state, ev.seq, "agent", textFromContent(ev.content), false);
      break;

    case "agent.thinking":
    case "agent.tool_result":
    case "agent.mcp_tool_result":
      break; // ignored (v1)

    case "agent.tool_use":
    case "agent.mcp_tool_use": {
      const name = String(ev.name ?? "");
      if (CUSTOM_TOOL_NAMES.has(name)) {
        // engine-agnostic rule: treat exactly as agent.custom_tool_use
        applyCustomTool(state, ev, name, input, toolUseId);
      } else {
        state.sincePlan++;
        state.feed.push({
          seq: ev.seq,
          kind: "tool",
          headline: `${name}: ${shortInput(input)}`,
          collapsed: true,
        });
      }
      break;
    }

    case "agent.custom_tool_use":
      applyCustomTool(state, ev, String(ev.name ?? ""), input, toolUseId);
      break;

    case "user.custom_tool_result": {
      const key = typeof ev.custom_tool_use_id === "string" ? ev.custom_tool_use_id : "";
      const i = state.pending.findIndex((q) => q.question_key === key);
      if (i !== -1) {
        // it was an ask_user: drop from pending, surface the answer in the feed
        state.pending.splice(i, 1);
        pushText(state, ev.seq, "user", textFromContent(ev.content), false);
      }
      // results for update_plan acks / submit_draft verdicts: no fold effect
      break;
    }

    case "gateway.judge_verdict": {
      const v: Verdict = {
        draft_id: String(ev.draft_id ?? ""),
        result: (ev.result as Verdict["result"]) ?? "needs_revision",
        explanation: String(ev.explanation ?? ""),
        iteration: typeof ev.iteration === "number" ? ev.iteration : 0,
        findings: Array.isArray(ev.findings) ? (ev.findings as JudgeFinding[]) : [],
        rubric: (ev.rubric ?? null) as Verdict["rubric"],
      };
      state.verdicts.push(v);
      {
        // parity with fold.py: body = explanation, key omitted when empty
        const item: FeedItem = { seq: ev.seq, kind: "verdict", headline: String(ev.result ?? ""), collapsed: false };
        if (v.explanation) item.body = v.explanation;
        state.feed.push(item);
      }
      break;
    }

    case "session.status_running":
      state.status = "working";
      break;

    case "session.status_idle": {
      const stopType = (ev.stop_reason as { type?: unknown } | undefined)?.type;
      if (stopType === "requires_action") {
        // needs_you derives from OUTSTANDING questions, not the idle event itself
        state.status = state.pending.length > 0 ? "needs_you" : "working";
      } else if (stopType === "end_turn") {
        state.status = "done";
      } else {
        state.status = "working";
        state.feed.push({ seq: ev.seq, kind: "system", headline: "paused", collapsed: false });
      }
      break;
    }

    case "session.status_terminated":
      if (state.status !== "done") state.status = "failed";
      break;

    case "session.error": {
      const msg = (ev.error as { message?: unknown } | undefined)?.message;
      state.feed.push({ seq: ev.seq, kind: "error", headline: String(msg ?? "error"), collapsed: false });
      break;
    }

    case "agent.thread_context_compacted":
      state.feed.push({ seq: ev.seq, kind: "system", headline: "context compacted", collapsed: false });
      break;

    case "span.model_request_end": {
      state.sincePlan++;
      const mu = (ev.model_usage ?? {}) as Record<string, unknown>;
      const n = (k: string): number => (typeof mu[k] === "number" ? (mu[k] as number) : 0);
      state.usage.input_tokens += n("input_tokens") + n("cache_read_input_tokens") + n("cache_creation_input_tokens");
      state.usage.output_tokens += n("output_tokens");
      state.usage.total_tokens = state.usage.input_tokens + state.usage.output_tokens;
      break;
    }

    case "reve.escalation":
      // whitelist exception (future-proofing, never emitted today): render as a feed item
      state.feed.push({ seq: ev.seq, kind: "system", headline: "escalation: deliverable held", collapsed: false });
      break;

    default:
      if (typeof ev.type === "string" && ev.type.startsWith("span.")) state.sincePlan++;
      break; // unknown types: ignored silently
  }
}

export function toSnapshot(state: FoldState): Snapshot {
  const hasActiveStep = state.planSteps?.some((s) => s.status === "active") ?? false;
  return {
    run_id: state.meta.run_id,
    engine: state.meta.engine,
    title: state.meta.title,
    status: state.status,
    cursor: state.cursor,
    plan:
      state.planSteps === null
        ? null
        : {
            steps: state.planSteps.map((s) => ({ ...s })),
            current_step_id: state.currentStepId,
            stale: hasActiveStep && state.sincePlan > STALE_THRESHOLD,
          },
    feed: state.feed.map((f) => ({ ...f })),
    pending_questions: state.pending.map((q) => ({ ...q })),
    drafts: state.drafts.map((d) => ({ ...d })),
    verdicts: state.verdicts.map((v) => ({ ...v })),
    usage: { ...state.usage },
  };
}

/**
 * Fold a full frame list into a Snapshot. Frames are first upserted by `id`
 * (the same id may re-arrive with `processed_at` flipped null→set; replace,
 * don't duplicate — CONTRACT §3 client rule), then folded in arrival order.
 */
export function foldEvents(frames: WireEvent[], meta: RunMeta): Snapshot {
  const byId = new Map<string, WireEvent>();
  let maxSeq = 0;
  for (const f of frames) {
    byId.set(f.id, f); // Map.set keeps first-insertion position: replace, don't duplicate
    if (typeof f.seq === "number") maxSeq = Math.max(maxSeq, f.seq);
  }
  const state = initialState(meta);
  for (const ev of byId.values()) applyEvent(state, ev);
  state.cursor = Math.max(state.cursor, maxSeq);
  return toSnapshot(state);
}

/** Rebuild fold state from a gateway snapshot so live SSE tailing can resume from its cursor. */
export function stateFromSnapshot(snap: Snapshot): FoldState {
  return {
    meta: { run_id: snap.run_id, engine: snap.engine, title: snap.title },
    status: snap.status,
    cursor: snap.cursor,
    planSteps: snap.plan ? snap.plan.steps.map((s) => ({ ...s })) : null,
    currentStepId: snap.plan ? snap.plan.current_step_id : null,
    feed: snap.feed.map((f) => ({ ...f })),
    pending: snap.pending_questions.map((q) => ({ ...q })),
    drafts: snap.drafts.map((d) => ({ ...d })),
    verdicts: snap.verdicts.map((v) => ({ ...v })),
    usage: { ...snap.usage },
    // preserve the snapshot's staleness verdict; exact counter value is unknowable here
    sincePlan: snap.plan?.stale ? STALE_THRESHOLD + 1 : 0,
  };
}
