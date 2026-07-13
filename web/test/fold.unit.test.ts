// Fixture-independent unit tests for the fold rules (CONTRACT §3).

import { describe, expect, it } from "vitest";
import { foldEvents, type RunMeta } from "../lib/fold";
import type { WireEvent } from "../lib/types";

const META: RunMeta = { run_id: "run_1", engine: "mock", title: "Test run" };

let seqCounter = 0;
function ev(type: string, payload: Record<string, unknown> = {}, id?: string): WireEvent {
  seqCounter++;
  return { seq: seqCounter, id: id ?? `evt_${seqCounter}`, type, processed_at: null, ...payload };
}

function reset() {
  seqCounter = 0;
}

describe("fold rules", () => {
  it("agent.message → feed with first-line headline and body", () => {
    reset();
    const snap = foldEvents([ev("agent.message", { content: [{ type: "text", text: "Headline\nrest of it" }] })], META);
    expect(snap.feed).toEqual([
      { seq: 1, kind: "agent", headline: "Headline", body: "rest of it", collapsed: false },
    ]);
  });

  it("upserts by id: re-arrival with processed_at flipped does not duplicate", () => {
    reset();
    const first = ev("agent.message", { content: [{ type: "text", text: "hi" }] }, "evt_x");
    const flipped = { ...first, seq: 5, processed_at: "2026-07-13T00:00:00Z" };
    const snap = foldEvents([first, flipped], META);
    expect(snap.feed).toHaveLength(1);
    expect(snap.cursor).toBe(5);
  });

  it("update_plan replaces the plan; ask_user appends a pending question keyed by tool id", () => {
    reset();
    const snap = foldEvents(
      [
        ev("agent.custom_tool_use", {
          name: "update_plan",
          input: {
            steps: [
              { id: "s1", title: "Research", status: "active" },
              { id: "s2", title: "Draft", status: "pending" },
            ],
            current_step_id: "s1",
          },
        }),
        ev("agent.custom_tool_use", {
          name: "ask_user",
          tool_use_id: "toolu_q1",
          input: { question: "Did you own incidents?", context: "JD wants on-call", kind: "open" },
        }),
        ev("session.status_idle", { stop_reason: { type: "requires_action", event_ids: ["toolu_q1"] } }),
      ],
      META,
    );
    expect(snap.plan).toEqual({
      steps: [
        { id: "s1", title: "Research", status: "active" },
        { id: "s2", title: "Draft", status: "pending" },
      ],
      current_step_id: "s1",
      stale: false,
    });
    expect(snap.pending_questions).toEqual([
      {
        question_key: "toolu_q1",
        question: "Did you own incidents?",
        context: "JD wants on-call",
        kind: "open",
        asked_seq: 2,
      },
    ]);
    expect(snap.status).toBe("needs_you"); // requires_action + outstanding question
  });

  it("idle requires_action with NO outstanding question stays working", () => {
    reset();
    const snap = foldEvents([ev("session.status_idle", { stop_reason: { type: "requires_action" } })], META);
    expect(snap.status).toBe("working");
  });

  it("custom_tool_result resolves the pending question and feeds the answer", () => {
    reset();
    const snap = foldEvents(
      [
        ev("agent.custom_tool_use", { name: "ask_user", tool_use_id: "toolu_q1", input: { question: "Q?" } }),
        ev("user.custom_tool_result", { custom_tool_use_id: "toolu_q1", content: "Yes, twice." }),
      ],
      META,
    );
    expect(snap.pending_questions).toEqual([]);
    expect(snap.feed).toEqual([{ seq: 2, kind: "user", headline: "Yes, twice.", collapsed: false }]);
  });

  it("custom_tool_result for a non-ask_user tool (auto-ack) has no fold effect", () => {
    reset();
    const snap = foldEvents([ev("user.custom_tool_result", { custom_tool_use_id: "toolu_plan", content: "ok" })], META);
    expect(snap.feed).toEqual([]);
  });

  it("submit_draft appends a draft (accepts input.text fallback); judge_verdict appends verdict + feed", () => {
    reset();
    const snap = foldEvents(
      [
        ev("agent.custom_tool_use", {
          name: "submit_draft",
          tool_use_id: "toolu_d1",
          input: { draft: "# Resume v1", label: "impact-forward" },
        }),
        ev("agent.custom_tool_use", { name: "submit_draft", tool_use_id: "toolu_d2", input: { text: "# Resume v2" } }),
        ev("gateway.judge_verdict", {
          draft_id: "toolu_d1",
          result: "needs_revision",
          explanation: "one fabrication",
          iteration: 1,
          findings: [{ span: "x", failure_mode: "fabrication", severity: "medium", rationale: "r" }],
          rubric: null,
        }),
      ],
      META,
    );
    expect(snap.drafts).toEqual([
      { draft_id: "toolu_d1", label: "impact-forward", draft: "# Resume v1", seq: 1 },
      { draft_id: "toolu_d2", label: "", draft: "# Resume v2", seq: 2 },
    ]);
    expect(snap.verdicts).toHaveLength(1);
    expect(snap.verdicts[0].result).toBe("needs_revision");
    expect(snap.feed.at(-1)).toEqual({ seq: 3, kind: "verdict", headline: "needs_revision", body: "one fabrication", collapsed: false });
  });

  it("tool_use with custom-tool NAME routes through the custom rule (engine-agnostic)", () => {
    reset();
    const snap = foldEvents(
      [ev("agent.tool_use", { name: "ask_user", tool_use_id: "toolu_q9", input: { question: "Q?" } })],
      META,
    );
    expect(snap.pending_questions).toHaveLength(1);
    expect(snap.feed).toEqual([]); // no tool feed item for the custom trio
  });

  it("ordinary tool_use feeds a collapsed headline", () => {
    reset();
    const snap = foldEvents([ev("agent.tool_use", { name: "web_search", input: { query: "acme corp" } })], META);
    expect(snap.feed).toEqual([
      { seq: 1, kind: "tool", headline: 'web_search: {"query":"acme corp"}', collapsed: true },
    ]);
  });

  it("status: end_turn → done; terminated → failed unless already done", () => {
    reset();
    const done = foldEvents([ev("session.status_idle", { stop_reason: { type: "end_turn" } })], META);
    expect(done.status).toBe("done");

    reset();
    const failed = foldEvents([ev("session.status_running"), ev("session.status_terminated")], META);
    expect(failed.status).toBe("failed");

    reset();
    const stillDone = foldEvents(
      [ev("session.status_idle", { stop_reason: { type: "end_turn" } }), ev("session.status_terminated")],
      META,
    );
    expect(stillDone.status).toBe("done");
  });

  it("unknown stop_reason → working with a 'paused' system note", () => {
    reset();
    const snap = foldEvents([ev("session.status_idle", { stop_reason: { type: "budget_exhausted" } })], META);
    expect(snap.status).toBe("working");
    expect(snap.feed).toEqual([{ seq: 1, kind: "system", headline: "paused", collapsed: false }]);
  });

  it("usage accumulates all four token fields into input/output/total", () => {
    reset();
    const snap = foldEvents(
      [
        ev("span.model_request_end", {
          model_usage: { input_tokens: 100, output_tokens: 50, cache_read_input_tokens: 10, cache_creation_input_tokens: 5 },
        }),
        ev("span.model_request_end", { model_usage: { input_tokens: 1, output_tokens: 2 } }),
      ],
      META,
    );
    expect(snap.usage).toEqual({ input_tokens: 116, output_tokens: 52, total_tokens: 168, usd: null });
  });

  it("plan goes stale after >15 countable events while a step is active; update_plan resets", () => {
    reset();
    const plan = ev("agent.custom_tool_use", {
      name: "update_plan",
      input: { steps: [{ id: "s1", title: "T", status: "active" }], current_step_id: "s1" },
    });
    const noise = Array.from({ length: 16 }, () =>
      ev("agent.message", { content: [{ type: "text", text: "..." }] }),
    );
    const stale = foldEvents([plan, ...noise], META);
    expect(stale.plan?.stale).toBe(true);

    reset();
    const fresh = foldEvents(
      [
        ev("agent.custom_tool_use", {
          name: "update_plan",
          input: { steps: [{ id: "s1", title: "T", status: "active" }] },
        }),
        ...Array.from({ length: 16 }, () => ev("agent.message", { content: [{ type: "text", text: "." }] })),
        ev("agent.custom_tool_use", {
          name: "update_plan",
          input: { steps: [{ id: "s1", title: "T", status: "active" }] },
        }),
      ],
      META,
    );
    expect(fresh.plan?.stale).toBe(false);
  });

  it("unknown event types are ignored; reve.escalation renders", () => {
    reset();
    const snap = foldEvents([ev("agent.some_future_thing", { data: 1 }), ev("reve.escalation", { reason: "held" })], META);
    expect(snap.feed).toHaveLength(1);
    expect(snap.feed[0].kind).toBe("system");
  });

  it("cursor = max seq over all frames, ignored types included", () => {
    reset();
    const snap = foldEvents([ev("agent.thinking"), ev("agent.unknown_type")], META);
    expect(snap.cursor).toBe(2);
  });
});
