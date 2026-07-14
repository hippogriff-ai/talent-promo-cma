# talent-promo-cma — Wire Contract (source of truth)

Both apps build against THIS file: the gateway (`gateway/`, this repo) and the frontend
(separate repo `talent-promo-web`, which vendors a synced copy of this file + the golden
fixtures — `make sync-fixtures`). When code and this file disagree, this file wins; change
it deliberately. Derived from
`docs/talent-promo-cma-spec.md` (v2.2) §2–§4 — read that for rationale.

## 1. Topology

```
Browser (talent-promo-web repo, :3000, no keys) ──SSE+REST──▶ Gateway (FastAPI :8100) ──▶ EngineAdapter
                                                 SQLite ./data/gateway.db      ├─ mock (no keys, scripted)
                                                 judge (OpenAI, or stub)       └─ cma  (anthropic SDK)
```

Web reads `NEXT_PUBLIC_API_URL` (default `http://localhost:8100`).

## 2. Gateway HTTP API (all JSON; errors use `{"type":"error","error":{"type","message"},"request_id"}`)

| Method+Path | Request | Response |
|---|---|---|
| `POST /api/coach/runs` | `{engine: "mock"\|"cma", title?: str, resume_text: str, job_text?: str, job_url?: str}` (at least one of job_text/job_url) | `201 {run_id: str}` |
| `GET /api/coach/runs` | — | `{runs: [RunSummary]}` newest first |
| `GET /api/coach/runs/{run_id}` | — | `Snapshot` (below) |
| `GET /api/coach/runs/{run_id}/events?cursor=N` | SSE | frames `data: <WireEvent JSON>\n\n`; `: heartbeat` comment every 15s; replays events with `seq > cursor` then tails live |
| `POST /api/coach/runs/{run_id}/messages` | `{text: str}` | `202 {}` |
| `POST /api/coach/runs/{run_id}/answers` | `{question_key: str, text?: str, skip?: bool}` (skip=true ⇒ answer text is `"[skipped — the candidate chose not to answer; move on]"`) | `202 {}`; `409` if key unknown/already answered (idempotent for UI: treat 409 as success-noop) |
| `POST /api/coach/runs/{run_id}/interrupt` | `{}` | `202 {}` |
| `GET /api/coach/runs/{run_id}/export` | — | Evidence bundle (§7) |
| `GET /api/coach/memory` | — | `{available: bool, memories: [{id, path, size_bytes, updated_at}]}` — `available:false` + empty for mock/no-key |
| `GET /api/coach/memory/{memory_id}` | — | `{id, path, content}` |

`RunSummary = {run_id, engine, title, status, created_at, needs_you: bool, spend_usd: float|null}`

### Snapshot (the Python fold's output; web fold must produce the identical shape from events)

```ts
type Snapshot = {
  run_id: string; engine: "mock"|"cma"; title: string;
  status: "working"|"needs_you"|"done"|"failed";
  cursor: number;                       // last folded seq; resume SSE from here
  plan: { steps: PlanStep[]; current_step_id: string|null; stale: boolean } | null;
  feed: FeedItem[];                     // display-ready, ordered
  pending_questions: Question[];        // CMA 0..n, mock 0..1
  drafts: Draft[];                      // ordered by submission
  verdicts: Verdict[];                  // parallel to drafts (draft_id)
  usage: { input_tokens: number; output_tokens: number; total_tokens: number; usd: number|null };
}
type PlanStep = { id: string; title: string; status: "pending"|"active"|"done"|"skipped"; note?: string }
type Question = { question_key: string; question: string; context?: string;
                  kind?: "open"|"confirm"|"choice"; options?: string[]; asked_seq: number }
type Draft    = { draft_id: string; label: string; summary?: string; draft: string; seq: number }
type Verdict  = { draft_id: string; result: "satisfied"|"needs_revision"; explanation: string;
                  iteration: number; findings: JudgeFinding[]; rubric: Record<string,{score:number,rationale:string}>|null }
type JudgeFinding = { span: string; failure_mode: string; severity: "low"|"medium"|"high"; rationale: string }
type FeedItem = { seq: number; kind: "user"|"agent"|"tool"|"system"|"verdict"|"error";
                  headline: string; body?: string; collapsed: boolean }
```

## 3. WireEvent (MA vocabulary; what the SSE carries)

Every frame: `{ seq: number, id: string, type: string, processed_at: string|null, ...payload }`.
`seq` is gateway-assigned, monotonically increasing per run (SSE cursor). `id` is the engine
event id (`sevt_*` CMA, `mockevt_*` mock) or `gwevt_*` for gateway-authored events.
**Client rule: upsert by `id`** (the same id may re-arrive with `processed_at` flipped null→set;
replace, don't duplicate). Unknown `type` ⇒ ignore silently (whitelist exception: `reve.escalation`
renders as a feed item — future-proofing, never emitted today).

### Types the fold consumes

| type | payload fields used | fold effect |
|---|---|---|
| `user.message` | `content: [{type:"text", text}]` | feed(kind=user) |
| `agent.message` | `content: [{type:"text", text}]` | feed(kind=agent, headline=first line, body=rest) |
| `agent.thinking` | — | ignored |
| `agent.tool_use` / `agent.mcp_tool_use` | `name, input, tool_use_id` | feed(kind=tool, headline=`{name}: {short input}`, collapsed) — **unless** `name ∈ {update_plan, ask_user, submit_draft}` → treat exactly as `agent.custom_tool_use` (engine-agnostic rule) |
| `agent.tool_result` / `agent.mcp_tool_result` | — | ignored (v1) |
| `agent.custom_tool_use` name=`update_plan` | `input.steps[], input.current_step_id` | replace `plan`; reset staleness counter |
| `agent.custom_tool_use` name=`ask_user` | `input.question, input.context?, input.kind?, input.options?` | append pending question, key = `tool_use_id ?? id` |
| `agent.custom_tool_use` name=`submit_draft` | `input.draft` (**also accept `input.text` fallback**), `input.label?, input.summary?` | append draft (draft_id = `tool_use_id ?? id`) |
| `user.custom_tool_result` | `custom_tool_use_id, content` | resolve matching pending question (drop from pending; feed kind=user with the answer text if it was an ask_user) |
| `gateway.judge_verdict` | `draft_id, result, explanation, iteration, findings[], rubric` | append verdict; feed(kind=verdict, headline=`result`, body=`explanation` — body key omitted when explanation empty) |
| `session.status_running` | — | status→working |
| `session.status_idle` | `stop_reason: {type, event_ids?}` | `requires_action` → needs_you (derived from *outstanding* questions); `end_turn` → done; anything unknown → working w/ "paused" feed note |
| `session.status_terminated` | — | failed (unless already done) |
| `session.error` | `error: {message}` | feed(kind=error); if terminal follows, failed |
| `agent.thread_context_compacted` | — | feed(kind=system, "context compacted") |
| `span.model_request_end` | `model_usage: {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}` | accumulate usage |

**Plan staleness:** `stale = (events with type agent.message/agent.tool_use*/span.* since last update_plan) > 15` while a step is `active`.

## 4. Custom tools (agent-side schemas — must match `infra/cma/talent-promo-coach.agent.yaml`)

- `update_plan {steps: [{id, title, status: pending|active|done|skipped, note?}], current_step_id?}` → gateway auto-acks `"ok"` instantly.
- `ask_user {question!, context?, kind?: open|confirm|choice, options?}` → gateway persists, holds; answer/skip returns the text as tool result.
- `submit_draft {draft!, label?, summary?}` → gateway runs judge, returns **tool result** = compact JSON `{result, explanation, findings: [...], rubric: {...}|null, instruction: "Address every finding..."}`, and emits `gateway.judge_verdict` on the wire.

**CMA batching:** one idle can block on MULTIPLE custom tool calls (`stop_reason.event_ids[]`) —
the gateway resolves each id by tool name; the session resumes only when all are answered.

## 5. Judge integration (gateway-side)

- `tp_gateway.judge` is vendored from talent-promo `apps/api/judge` @ gepa-prep (see `gateway/tp_gateway/judge/VENDORED.md`). Entry: `run_judge(inputs: JudgeInput, prompts, client, model, include_rubric=True)`.
- JudgeInput render (trusted sources ONLY — spec §6.1): `source_profile` = run's resume_text + all gateway-recorded Q&A (verbatim, `Q: ...\nA: ...` blocks); `job_posting` = job_text (or fetched text); `research_findings` + `gap_analysis` = latest agent-provided contract content — v1: taken from the `submit_draft` turn's preceding memory writes if CMA, else from optional `input.research_notes`/`input.gap_analysis` fields if present, else `"(none provided)"`; `generated_resume` = `input.draft`.
- **Stub mode:** if `OPENAI_API_KEY` unset (or `TP_JUDGE_STUB=1`), use the deterministic stub: first submission of a run → `needs_revision` with one canned medium `fabrication` finding quoting any sentence of the draft; subsequent submissions → `satisfied`. Mock runs must work with zero keys.
- Persist with every verdict: the exact rendered JudgeInput, judge model, prompt version (`load_judge_prompts().version`).

## 6. Engines

### mock (`engines/mock.py`) — must run with ZERO keys
Scripted async generator producing the golden run (also saved as `gateway/tests/fixtures/mock_run.jsonl`):
1. `session.status_running`
2. `update_plan` (5 steps: ingest ✓/research/interview/draft/deliver; current=research)
3. `agent.message` (research narration) + `agent.tool_use` name=web_search + `span.model_request_end`
4. `update_plan` (research done, interview active)
5. `ask_user` {question about an under-evidenced JD requirement, context, kind=open} + `session.status_idle {requires_action, event_ids:[that id]}`
6. — blocks until an answer arrives via adapter.answer() —
7. `user.custom_tool_result` (the answer) + `session.status_running`
8. `agent.message` (what the answer unlocks) + `update_plan` (interview done, draft active)
9. `submit_draft` {draft: markdown resume incorporating the answer, label:"impact-forward"} + idle requires_action
10. — gateway judges (stub: needs_revision) → tool result —
11. `agent.message` (revising) + `submit_draft` (revised) — judged satisfied
12. `update_plan` (all done) + `agent.message` (final summary) + `session.status_idle {end_turn}`

Timing: ~0.5–1.5s between events (config `TP_MOCK_DELAY_MS`, 0 in tests). Supports `send_message`
(appends a user.message + short agent.message reply), `interrupt` (→ idle interrupted → done).

### cma (`engines/cma.py`)
- `anthropic` SDK, `client.beta.sessions.*`. Env: `ANTHROPIC_API_KEY`, `CMA_AGENT_ID` (+ optional `CMA_AGENT_VERSION`), `CMA_ENVIRONMENT_ID`, `CMA_MEMORY_STORE_ID`.
- create: `sessions.create(agent=..., environment_id=..., resources=[{type:"memory_store", memory_store_id, access:"read_write", instructions:...}], metadata={run_id}, title=...)`, then send kickoff `user.message` (resume + job + charter-kickoff text).
- events: **stream-first, then reconcile**: open `events.stream`, fetch `events.list`, dedupe/upsert by id (NO server replay — gateway's SQLite is the replay). Reconnect with the same consolidation on every drop.
- On idle `requires_action`: dispatch every `stop_reason.event_ids` entry per §4.
- Memory endpoints use beta header `agent-memory-2026-07-22` (SDK handles if current; NEVER send it together with `managed-agents-2026-04-01` on memory calls).
- Console URL log line on create: `https://platform.claude.com/workspaces/{CMA_WORKSPACE_ID or 'default'}/sessions/{id}`.

## 7. Evidence bundle (`GET .../export`) — consumed by talent-promo-eval

```json
{
  "run": RunSummary & {resume_text, job_text, job_url, agent_ref: {engine, agent_id?, agent_version?, model?}},
  "events": [WireEvent...],                       // full ordered log incl. gateway.* events
  "qa": [{question_key, question, context?, answer, skipped, asked_at, answered_at}],
  "drafts": [Draft...],
  "verdicts": [Verdict & {judge_input: {...5 fields...}, judge_model, prompt_version}],
  "plan_history": [{seq, steps, current_step_id}],
  "usage": UsageNorm,
  "exported_at": iso8601
}
```

## 8. Config / env

| var | default | notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | dedicated-workspace key (op.env → `make secrets`) |
| `OPENAI_API_KEY` | — | judge; absent ⇒ judge stub |
| `CMA_AGENT_ID` / `CMA_AGENT_VERSION` / `CMA_ENVIRONMENT_ID` / `CMA_MEMORY_STORE_ID` / `CMA_WORKSPACE_ID` | — | from `infra/cma/setup.sh` output |
| `TP_DB_PATH` | `./data/gateway.db` | SQLite |
| `TP_DEFAULT_ENGINE` | `mock` | |
| `TP_JUDGE_STUB` | auto | force `1`/`0` |
| `TP_MOCK_DELAY_MS` | `800` | 0 in tests |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8100` | web → gateway |

## 9. Golden fold test (both languages)

`gateway/tests/fixtures/mock_run.jsonl` (one WireEvent per line, seq included, with the sample
answer applied) must fold to `gateway/tests/fixtures/mock_run.snapshot.json` — asserted by
`gateway/tests/test_fold.py` (Python fold) AND `web` vitest (TS fold). Byte-identical JSON
(sorted keys, no floats where ints do).
