"""Pure fold: ordered WireEvents -> Snapshot (CONTRACT.md §3).

Golden-tested against gateway/tests/fixtures/ and mirrored by the web TS fold,
so every rule here must stay deterministic and language-portable:

- optional contract fields (`x?:`) are OMITTED when absent; `X|null` fields are
  emitted as explicit null (see models.py docstring);
- headline/body split: first line is the headline, the stripped remainder is
  the body (omitted when empty);
- tool feed headline: `{name}: {preview}` where preview is `input.path` ??
  `input.query` ?? `input.url` (first non-empty string wins) ?? the compact
  JSON of the input with SORTED keys — truncated to 80 chars with a trailing
  "..." (TS side must sort keys too); tool feed body is the pretty-printed
  2-space sorted-keys JSON of the input;
- usage accumulates all input-side token fields (input + cache_read +
  cache_creation) into input_tokens; usd is always null in v1 (no price table).
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from tp_gateway.models import Draft, FeedItem, Inputs, Plan, Question, Snapshot, Usage, Verdict

CUSTOM_TOOL_NAMES = {"update_plan", "ask_user", "submit_draft"}

# Plan staleness (§3): these event types count against the last update_plan.
# Threshold raised 15→40: CMA agents legitimately emit long memory-writing
# tool streaks between plan updates.
_STALENESS_TYPES = {"agent.message", "agent.tool_use", "agent.mcp_tool_use"}
_STALENESS_THRESHOLD = 40

# §3 kickoff feed rule: the run's FIRST user.message (the kickoff carrying
# resume+job) folds to this headline, collapsed, with NO body — the UI renders
# the inputs from Snapshot.inputs, never from the kickoff blob.
KICKOFF_HEADLINE = "Run inputs — resume & job posting"


def _text_of(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content if isinstance(b, Mapping) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def _headline_body(text: str) -> tuple[str, str | None]:
    text = text.strip()
    if "\n" not in text:
        return text, None
    head, rest = text.split("\n", 1)
    rest = rest.strip()
    return head.strip(), rest or None


def _truncate(s: str) -> str:
    return s if len(s) <= 80 else s[:77] + "..."


def _tool_preview(tool_input: Any) -> str:
    """§3 tool row: preview = input.path ?? input.query ?? input.url (first
    non-empty string wins) ?? compact sorted-keys JSON; 80-char truncation."""
    if isinstance(tool_input, Mapping):
        for key in ("path", "query", "url"):
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                return _truncate(v)
    return _truncate(json.dumps(tool_input or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _tool_body(tool_input: Any) -> str:
    """§3 tool row body: pretty-printed 2-space sorted-keys JSON of the input."""
    return json.dumps(tool_input or {}, sort_keys=True, indent=2, ensure_ascii=False)


def _feed_item(seq: int, kind: str, headline: str, body: str | None = None, collapsed: bool = False) -> FeedItem:
    item: FeedItem = {"seq": seq, "kind": kind, "headline": headline, "collapsed": collapsed}
    if body:
        item["body"] = body
    return item


def _dedupe_by_id(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Upsert by id: keep the first occurrence's position+seq, the last payload."""
    ordered: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for ev in events:
        eid = str(ev.get("id", ""))
        if eid and eid in index:
            i = index[eid]
            ordered[i] = {**dict(ev), "seq": ordered[i].get("seq", ev.get("seq", 0))}
        else:
            if eid:
                index[eid] = len(ordered)
            ordered.append(dict(ev))
    return ordered


def fold(
    run_id: str,
    engine: str,
    title: str,
    events: Iterable[Mapping[str, Any]],
    inputs: Inputs | None = None,
) -> Snapshot:
    # inputs is gateway-injected meta from the run row (§2), NOT derived from
    # events; callers without a run row (unit tests) get the empty shape.
    snapshot_inputs: Inputs = (
        {"resume_text": inputs["resume_text"], "job_text": inputs["job_text"], "job_url": inputs["job_url"]}
        if inputs is not None
        else {"resume_text": "", "job_text": "", "job_url": None}
    )
    plan: Plan | None = None
    feed: list[FeedItem] = []
    pending: list[Question] = []
    drafts: list[Draft] = []
    verdicts: list[Verdict] = []
    status = "working"
    cursor = 0
    in_tokens = 0
    out_tokens = 0
    events_since_plan = 0
    kickoff_folded = False

    def custom_tool_use(ev: dict[str, Any], seq: int) -> None:
        nonlocal plan, events_since_plan
        name = ev.get("name")
        tool_input = ev.get("input") or {}
        key = ev.get("tool_use_id") or ev.get("id")
        if name == "update_plan":
            raw_steps = tool_input.get("steps")
            if isinstance(raw_steps, str):
                # live agents sometimes JSON-encode the whole array (custom tools
                # are not strict-validated) — exactly ONE parse attempt; NEVER
                # iterate a string's characters (§3 tolerance)
                try:
                    parsed = json.loads(raw_steps)
                except ValueError:
                    parsed = None
                if not isinstance(parsed, list):
                    # second (last) recovery: live CMA has emitted the array wrapped in
                    # tool-call artifacts (e.g. '<parameter name="steps">[...]') — extract
                    # the outermost [...] substring and parse once (§3 tolerance)
                    i, j = raw_steps.find("["), raw_steps.rfind("]")
                    if 0 <= i < j:
                        try:
                            parsed = json.loads(raw_steps[i : j + 1])
                        except ValueError:
                            parsed = None
                raw_steps = parsed if isinstance(parsed, list) else []
            elif not isinstance(raw_steps, list):
                raw_steps = []
            steps = []
            for s in raw_steps:
                if isinstance(s, str):
                    # bare step titles — degrade gracefully, never crash the fold
                    steps.append({"id": s, "title": s, "status": "pending"})
                    continue
                if not isinstance(s, dict):
                    continue
                step = {"id": str(s.get("id", "")), "title": str(s.get("title", "")), "status": s.get("status", "pending")}
                if s.get("note"):
                    step["note"] = s["note"]
                steps.append(step)
            # deterministic id dedupe (§3): first occurrence keeps its id, later
            # duplicates get "#2", "#3"… (count per base id); current_step_id
            # therefore refers to the first occurrence
            counts: dict[str, int] = {}
            for step in steps:
                base = step["id"]
                n = counts.get(base, 0) + 1
                counts[base] = n
                if n > 1:
                    step["id"] = f"{base}#{n}"
            plan = {"steps": steps, "current_step_id": tool_input.get("current_step_id"), "stale": False}
            events_since_plan = 0
        elif name == "ask_user":
            q: Question = {"question_key": str(key), "question": tool_input.get("question", ""), "asked_seq": seq}
            if tool_input.get("context"):
                q["context"] = tool_input["context"]
            if tool_input.get("kind"):
                q["kind"] = tool_input["kind"]
            if tool_input.get("options"):
                q["options"] = list(tool_input["options"])
            pending.append(q)
        elif name == "submit_draft":
            # input.draft, with input.text accepted as fallback (contract §3)
            d: Draft = {
                "draft_id": str(key),
                "label": tool_input.get("label") or "draft",
                "draft": tool_input.get("draft") or tool_input.get("text") or "",
                "seq": seq,
            }
            if tool_input.get("summary"):
                d["summary"] = tool_input["summary"]
            drafts.append(d)

    for ev in _dedupe_by_id(events):
        t = ev.get("type", "")
        seq = int(ev.get("seq", 0))
        cursor = max(cursor, seq)
        if t in _STALENESS_TYPES or t.startswith("span."):
            events_since_plan += 1

        if t == "user.message":
            if not kickoff_folded:
                # §3 kickoff feed rule: first user.message is the inputs blob
                kickoff_folded = True
                feed.append(_feed_item(seq, "user", KICKOFF_HEADLINE, collapsed=True))
            else:
                head, body = _headline_body(_text_of(ev.get("content")))
                feed.append(_feed_item(seq, "user", head, body))
        elif t == "agent.message":
            head, body = _headline_body(_text_of(ev.get("content")))
            feed.append(_feed_item(seq, "agent", head, body))
        elif t in ("agent.tool_use", "agent.mcp_tool_use"):
            # Engine-agnostic rule: the three custom tools fold as custom_tool_use
            # even when they arrive as plain tool_use (reve hands).
            if ev.get("name") in CUSTOM_TOOL_NAMES:
                custom_tool_use(ev, seq)
            else:
                headline = f"{ev.get('name', 'tool')}: {_tool_preview(ev.get('input'))}"
                feed.append(_feed_item(seq, "tool", headline, _tool_body(ev.get("input")), collapsed=True))
        elif t == "agent.custom_tool_use":
            custom_tool_use(ev, seq)
        elif t == "user.custom_tool_result":
            use_id = str(ev.get("custom_tool_use_id", ""))
            for i, q in enumerate(pending):
                if q["question_key"] == use_id:
                    del pending[i]
                    head, body = _headline_body(_text_of(ev.get("content")))
                    feed.append(_feed_item(seq, "user", head, body))
                    break
        elif t == "gateway.judge_verdict":
            # legacy events stored before 2026-07-17 carry the agent-facing
            # imperative suffix — strip it for display (parity with fold.ts)
            explanation = str(ev.get("explanation", ""))
            if explanation.endswith("; address each and resubmit."):
                explanation = explanation[: -len("; address each and resubmit.")] + "."
            verdict: Verdict = {
                "draft_id": str(ev.get("draft_id", "")),
                "result": str(ev.get("result", "")),
                "explanation": explanation,
                "iteration": int(ev.get("iteration", 0)),
                "findings": list(ev.get("findings") or []),
                "rubric": ev.get("rubric"),
            }
            verdicts.append(verdict)
            feed.append(_feed_item(seq, "verdict", verdict["result"], verdict["explanation"] or None))
        elif t == "session.status_running":
            status = "working"
        elif t == "session.status_idle":
            stop = ev.get("stop_reason") or {}
            stop_type = stop.get("type")
            if stop_type == "requires_action":
                # needs_you derives from OUTSTANDING questions, not the idle itself
                status = "needs_you" if pending else "working"
            elif stop_type == "end_turn":
                status = "done"
            else:
                status = "working"
                feed.append(_feed_item(seq, "system", "paused"))
        elif t == "session.status_terminated":
            if status != "done":
                status = "failed"
        elif t == "session.error":
            err = ev.get("error") or {}
            feed.append(_feed_item(seq, "error", str(err.get("message", "error"))))
        elif t == "agent.thread_context_compacted":
            feed.append(_feed_item(seq, "system", "context compacted"))
        elif t == "span.model_request_end":
            mu = ev.get("model_usage") or {}
            in_tokens += (
                int(mu.get("input_tokens") or 0)
                + int(mu.get("cache_read_input_tokens") or 0)
                + int(mu.get("cache_creation_input_tokens") or 0)
            )
            out_tokens += int(mu.get("output_tokens") or 0)
        elif t == "reve.escalation":
            # whitelist exception (§3): renders as a feed item; never emitted today
            head = str(ev.get("reason") or ev.get("message") or "escalation")
            feed.append(_feed_item(seq, "system", f"reve escalation: {head}"))
        # anything else: ignore silently (agent.thinking, tool results, unknown types)

    if plan is not None:
        any_active = any(s.get("status") == "active" for s in plan["steps"])
        plan["stale"] = any_active and events_since_plan > _STALENESS_THRESHOLD

    usage: Usage = {
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "total_tokens": in_tokens + out_tokens,
        "usd": None,
    }
    return {
        "run_id": run_id,
        "engine": engine,
        "title": title,
        "inputs": snapshot_inputs,
        "status": status,  # type: ignore[typeddict-item]
        "cursor": cursor,
        "plan": plan,
        "feed": feed,
        "pending_questions": pending,
        "drafts": drafts,
        "verdicts": verdicts,
        "usage": usage,
    }


def snapshot_json(snapshot: Snapshot) -> str:
    """Canonical serialization for the golden fold test: sorted keys, 2-space
    indent, no ASCII escaping, trailing newline. The TS fold test must match."""
    return json.dumps(snapshot, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
