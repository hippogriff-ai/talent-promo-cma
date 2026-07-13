# talent-promo-cma — Spec (functional / architecture / restrictions / eval)

**Status:** v2.2 — post-review + owner rulings (Appendix C). **All §9 questions resolved; ready for P0.**
**Date:** 2026-07-13. **Author:** Claude. Provenance: 3-agent research sweep (talent-promo working tree, reve source, MA reference) → draft → 4-lens adversarial review (47 findings: 4 blocker / 15 major, all incorporated — Appendix B) → owner interview (Appendix C).

**Goal (unchanged):** user provides their current resume + dream job (link or pasted posting). The agent acts like a career coach: it researches, then **interviews the user to surface talent and past experience they don't realize is an asset** — this discovery is the product's value; drafting is downstream of it. The workflow is no longer hard-coded: the agent plans its own steps, the UI renders that plan live, funnels the agent's questions to the user, and shows drafts for review and export.

**Scope (owner ruling 2026-07-13): this work builds talent-promo-cma only.** reve appears throughout this spec as *context and design target* — its P9 D4 ruling intentionally matches Managed Agents wire shapes, so defining the UI contract in the MA event vocabulary keeps the backend swappable for a future **talent-promo-reve** at near-zero extra cost. That future build (former P5) is **out of this work's scope**; the reve columns/notes below are recorded so the CMA build doesn't paint it into a corner, nothing more.

---

## 0. Decisions up front (tweak these)

| # | Decision | Recommendation | Main alternative |
|---|---|---|---|
| D1 | Code layout | **FLIPPED (owner directive 2026-07-13): standalone repo `~/tech/talent-promo-cma`.** The monorepo's rationale collapsed once reve left scope (no shared-UI consumer) and its costs stayed (main auto-deploys to prod; judge/evals untracked; pnpm-8 pins). The engine-adapter seam, MA wire contract, and one-UI design move to the new repo unchanged (`CONTRACT.md` there); the judge is vendored (`gateway/tp_gateway/judge/VENDORED.md` sync policy); D12 (branch dance in this repo) becomes moot. | ~~One monorepo behind `/coach` routes~~ — superseded. |
| D2 | UI wire protocol | **MA event vocabulary is the UI's native protocol.** Browser consumes `{id, type, processed_at, ...}` events relayed by our gateway. This **retires** the v2 doc's "keep the flat `{type, phase, message}` SSE envelope" ruling — that envelope can drive neither engine. | Invent a third normalized schema — rejected: reve already speaks MA shapes; a third schema means two translations instead of ~zero. |
| D3 | Dynamic stages | **Agent-authored plan via an `update_plan` custom tool** (Claude-Code-TodoWrite pattern). Gateway adds a cheap staleness heuristic (§3.2) so a silent agent can't leave the strip lying. | Derive stages heuristically from tool-use events — rejected as primary (fragile), kept only as the staleness signal. |
| D4 | Q&A funneling | **`ask_user` custom tool** → session idles `requires_action` → UI question card → answer returns as `user.custom_tool_result`. One question per call; related sub-questions may be grouped in the question text; CMA may issue parallel calls (dock renders multiple cards), reve holds at most one pending question (§2.2). | Free-form chat only — rejected: loses typed questions, blocking semantics, and the answer→claim pipeline. |
| D5 | Memory | **CMA memory store = the product's memory** (one `read_write` store for the single user, mounted every product session; eval runs get isolated stores, §7). Engine-neutral relative-path layout (§4.4). **Note: no server-side memory exists in the repo today** — browser IndexedDB + the judge's `source_profile` contract are all there is; this layer is new, not an integration. **[OWNER-CONFIRMED 2026-07-12]** | Own Postgres claim-corpus projected into each engine — the eventual V2 shape; overkill for v1 single-user. |
| D6 | Session granularity | **One session per application run** (resume × job). Memory carries cross-session knowledge. Completed sessions reusable for follow-ups — but sandbox checkpoints expire after 30 days, so **anything needed for follow-ups must live in memory, not the sandbox** (charter rule). | One long-lived session per user — rejected: conflates applications, bloats context, fights compaction. |
| D7 | Drafting + judge | **`submit_draft` custom tool.** On CMA, the gateway runs `apps/api/judge` on each submission and returns the verdict as the tool result (revise-until-clean). **On reve this is impossible** — `submit_draft` is a pack hand judged in-loop by the pack's own gate (haiku + rubric.md, floor 0.60); the gateway has no channel to answer it. So: in-loop judging is engine-owned; **the eval scores all arms with a held-out judge configuration** (§7.3) to kill the train-on-test asymmetry. | CMA Outcomes (`user.define_outcome` + Anthropic's opaque grader) — deferred; transparent GEPA-aligned judging is the differentiator. Keep as an experiment axis. |
| D8 | Gateway persistence | **SQLite** (single user, local + small): `runs`, `event_cache`, `plans`, `questions`, `drafts`, `verdicts` (with rendered JudgeInput per verdict), reve `caller_token`s. | Postgres — adopt when a second user or hosted deploy appears. |
| D9 | Model | **`claude-opus-4-8`** on the agent; in-loop judge stays `gpt-5-mini`. **Owner rule: judge/eval models stay in the OpenAI family.** For the eval, reve's pack `models.default` is set to the same model as the CMA agent (or the delta is declared as an asymmetry, §7.3). | Fable 5 (`claude-fable-5`) — stronger long-horizon work at ~2× token price; candidate extra eval arm (owner to decide later, in the eval repo). |
| D10 | Rollout | **New routes behind the existing app** (`/coach`, `/coach/run/[id]`; API under `/api/coach/*`). Nothing on main's existing pages changes → safe with Vercel auto-deploy (**owner-confirmed: main auto-deploys to prod** — every merge is a de-facto release; main must always stay shippable). Old OpenAI/Temporal path untouched until the eval says otherwise. | Replace homepage — rejected: repeats the archived branch's merge hazard. |
| D11 | Hosting (v1) | **Local-only.** Web + gateway run on the owner's machine (`start.sh` pattern, minus Temporal); the deployed Vercel site hides `/coach` behind an env flag (`NEXT_PUBLIC_COACH_ENABLED`). The gateway holds keys + SQLite and is never publicly exposed in v1. **[OWNER-CONFIRMED 2026-07-12]** | Host the gateway (Fly, like reve) and point `NEXT_PUBLIC_API_URL` at it — adopt when the owner wants the deployed site to run coach sessions. |
| D12 | Branch base | **`feat/cma-engine` branches off `gepa-prep`** — the judge module, evals/, and docs/ are *untracked, existing on no branch*; branching off main would strand the judge (D7) and datagen (§7). First queued commit: add judge + evals + docs (per the work-hours rule). | Commit gepa-prep to main first, then branch — equivalent; owner's call on PR hygiene. |

---

## 1. Functional spec

### 1.1 The flow (emergent, not hard-coded)

There are no fixed stages anymore. The agent receives a charter (system prompt) stating goals and constraints — **not** steps — and publishes its own plan via `update_plan` as its first action and whenever the plan changes. A typical self-authored plan will resemble the old stages (ingest → research → discovery interview → draft → revise → deliver), but the agent may reorder, add, split, or skip steps (e.g., skip research if memory already covers the company; add a "clarify seniority framing" step mid-run). The UI renders whatever the plan currently is.

### 1.2 What the user sees / how they interact

**Run screen = three zones + a status banner** (v1 targets desktop; the question card and status banner must remain usable at narrow widths — dock stacks above feed; full mobile deferred):

1. **Plan strip** (replaces StageStrip): the agent's current `steps[]` with status pending/active/done/skipped — "how many steps we think we need right now," updating live. If tool/model activity continues well past the last `update_plan` while a step is `active`, the strip dims with "plan may be stale" (§3.2 heuristic).
2. **Activity feed**: human-readable event stream (agent narration, tool activity, compaction notices, judge verdicts, reve escalations). Collapsed to headline items; expandable.
3. **Interaction dock** — the funnel:
   - **Question card(s)** on `ask_user`: question, why-it-matters context, optional choices, free-text answer, and **Skip** ("I don't know / skip" is an answer, not silence). CMA may have several cards pending at once (answered independently, per tool id; the run resumes when the last one is resolved); reve shows at most one.
   - **Draft review** on `submit_draft`: version list + diff vs. original, judge verdict inline (from the gateway snapshot — the wire event carries only the summary), accept / request changes.
   - **Free chat** otherwise: user can steer any time (`user.message`), including while the agent runs (messages queue).

**Status banner:** `working` / `needs you` (derived from *outstanding* ask_user ids, not merely from an idle event) / `done` / `failed`, plus token spend.

**Lifecycle edges (specified, not implied):**
- *Browser close mid-question:* reload = snapshot + tail from gateway cursor; the question is still there.
- *Abandonment:* a run in "needs you" for >7 days surfaces a "still waiting — answer or skip?" nudge in the run list; the owner can skip-and-continue or archive the run (gateway archives the underlying session only when the run is explicitly closed).
- *Failure:* `terminated` / `retries_exhausted` is irreversible at the session level. The **failed** banner offers "retry this application" = new session under the same `run_id`, reusing memory + prior recorded answers (re-injected into the kickoff); never silently re-billed.

### 1.3 Discovery — the value proposition, made concrete

The charter directs the agent to run a **gap-driven interview**, not a questionnaire:

- Research the JD/company first; diff requirements against the resume **and memory**; every question must be motivated by a gap or an under-evidenced claim ("The JD leans on incident response; your resume never mentions on-call — did you ever own production incidents, even informally?").
- Chase **adjacent/hidden experience**: side projects, informal leadership, cross-functional work, quantifiable outcomes the candidate never wrote down.
- Each answer is distilled into a **claim file** in memory (§4.4) with provenance (`user-confirmed`, date, verbatim quote). Claims persist across applications. **Never re-ask what memory already answers.**
- Question budget: ~3–7 high-value asks per run; group tightly-related sub-questions into one question text rather than spraying calls.

### 1.4 Drafting, judging, export

- Drafts must be grounded: every candidate-fact traces to the resume, a user answer, or a **user-confirmed** memory claim (asymmetric grounding rule: source profile authorizes what the resume claims; JD/research only shape framing/vocabulary). Agent-inferred memory text does **not** authorize claims (§6.1) — unverifiable claims escalate via `ask_user` ("confirm or I cut it").
- CMA: `submit_draft` → gateway renders JudgeInput from **trusted sources** (§6.1), runs grounding judge + rubric → verdict as tool result → revise until clean or escalated. reve: the pack's own gate judges in-loop; a below-floor draft emits `reve.escalation` (held deliverable), which the UI **renders** as a draft-held notice — it is the one `reve.*` type on the whitelist.
- Deliverable: 1–N versions `{id, label, summary, text}` in the diff-review UI (original as stable base). **Export = markdown/text download from the gateway drafts table (client-side) — owner-confirmed as the v1 scope.** The docx-skill path (`/mnt/session/outputs/` + `files.list(scope_id=…)` with both beta headers) is recorded as a future option, out of scope.

### 1.5 Memory behavior (user-visible)

- **"What the coach knows about me"** page (host-side memories API): browse/edit/delete claim files and preferences. Owner edits count as user-confirmation. Versions give a 30-day-bounded audit trail (§5.5); rollback = retrieve old version + write back (no restore endpoint); scrubbing a leaked secret = overwrite/delete the memory **then** redact the old version (a head version can't be redacted).
- **Cold start (run #1):** the charter instructs the agent to distill `profile/master.md` from the resume if absent; the gateway's JudgeInput render tolerates missing files (resume-only grounding on run #1); `/coach/memory` empty state: "the coach hasn't learned anything yet — start a run."
- The agent reads memory at run start and writes claims/lessons as it learns; Q&A answers upgrade unverified claims to user-confirmed.

---

## 2. Architecture

### 2.1 Topology

```
Browser (Next.js, no keys) — /coach routes behind NEXT_PUBLIC_COACH_ENABLED
   │  SSE + REST (MA-shaped events, gateway-owned replay)
   ▼
FastAPI gateway (apps/api, runs locally in v1 — D11)
   │  holds: ANTHROPIC_API_KEY, REVE_PROVISIONING_KEY, reve caller_tokens
   │  owns: SQLite (runs/events/plans/questions/drafts/verdicts)
   │  runs: judge (apps/api/judge, OpenAI) on CMA submit_draft
   ├── EngineAdapter protocol
   │     ├── CmaAdapter    → api.anthropic.com  client.beta.sessions.* (SDK)
   │     └── ReveAdapter   → reve-api.fly.dev   (httpx; later)
   ▼
Engines: CMA session-per-run + memory store  /  reve session + scope
```

**Why a gateway (not browser→engine):** browsers can't hold the Anthropic org key or reve's provisioning key; CMA's SSE has no replay (gateway consolidates via list+dedupe and re-serves with its own cursor); custom tools need a server to answer; the run registry has to live somewhere. Temporal is **not needed** on the CMA path. The existing Temporal/OpenAI path is untouched.

### 2.2 EngineAdapter protocol

```python
class EngineAdapter(Protocol):
    async def create_run(self, spec: RunSpec) -> RunHandle
    async def events(self, h: RunHandle, cursor: str|None) -> AsyncIterator[WireEvent]  # consolidated, replayable
    async def send_message(self, h: RunHandle, text: str) -> None
    async def answer(self, h: RunHandle, question_key: str, content: str) -> None
    async def interrupt(self, h: RunHandle) -> None
    async def usage(self, h: RunHandle) -> UsageNorm
```

`WireEvent` **is** the MA shape. Adapter deltas (verified against reve source and live MA docs):

| Concern | CMA | reve | Adapter/UI rule |
|---|---|---|---|
| Auth | org `x-api-key` (SDK) | `X-Provisioning-Key` (mgmt) + per-session `caller_token` shown **once** at create, no reissue | persist caller_token in SQLite at create or the run is send-dead forever |
| Create | `{agent, environment_id, resources[], metadata}`, then first `user.message` | `{pack, message(required), scope_id, budget_tokens?(default 100k), title}` | adapter folds kickoff into create (reve) / events.send (CMA) |
| Reconnect | **no replay** → `events.list` + dedupe by id | `?since_seq=` replay | gateway keeps its own event cache; browser always resumes from gateway cursor |
| processed_at | same event re-appears with flip | same (re-emitted under same id) | **upsert by id**, never dedupe-and-drop |
| Statuses | idle/running/rescheduling/terminated; also tolerate `session.updated` / `session.deleted` (deleted closes the relay) | same minus rescheduling; extra stop_reasons `budget_exhausted`, `interrupted` | UI keys on `status_idle.stop_reason.type == "requires_action"`; tolerate unknown stop_reasons |
| Question identity | `agent.custom_tool_use` has a tool id; `stop_reason.event_ids[]` lists **all** blocking events | ask_user event carries **no** tool_use_id — the wire event id is the key; answers bind to the *latest pending* question (sent id ignored) | engine-agnostic question key = "tool id if present, else wire event id"; persist both; ReveAdapter.answer discards the key |
| Pending questions | 0..n (parallel custom tool calls; resolve **every** id in `event_ids`) | 0..1 (first ask parks the loop; one at a time) | dock renders n cards on CMA, ≤1 on reve |
| Extra types | `event_deltas` previews (opt-in), threads, tool_confirmation | `reve.*` passthrough | ignore unknown types **except** `reve.escalation`, which renders (§1.4) |
| Send response | event echoes | `{"accepted":[...]}` | fire-and-confirm; read results off the stream |
| Errors | MA envelope | identical envelope; **but** reve 409s on answer-without-pending-question (incl. double-submit) and message-to-exhausted/cancelled | one parser; answers route maps reve 409 to idempotent no-op + UI notice |
| Usage | `sessions.retrieve().usage` (4 token fields — simplest spend source) | session usage = single `total_tokens`; richer per-turn in `span.model_request_end` | normalize to `UsageNorm` (§7.2 M6) |

### 2.3 Gateway API (browser-facing)

```
POST /api/coach/runs                {engine, resume_text|file_id, job_url|job_text, title?} → {run_id}
GET  /api/coach/runs/{id}           snapshot: status, plan, pending_questions[], drafts[], verdicts[], usage
GET  /api/coach/runs/{id}/events    SSE, MA-shaped, ?cursor= (gateway replay, both engines)
POST /api/coach/runs/{id}/messages  {text}
POST /api/coach/runs/{id}/answers   {question_key, text | choice | skip}
POST /api/coach/runs/{id}/interrupt
GET  /api/coach/memory              memory browser (list/read)      [CMA memories API]
PATCH/DELETE /api/coach/memory/...  edit/delete + write-then-redact flow
GET  /api/coach/runs/{id}/export    per-run evidence bundle for the eval repo (§7.5)
                                    (draft download itself is client-side from the snapshot's drafts[])
```

The **snapshot endpoint** exists so the UI never reconstructs state from the log alone (reload = snapshot + tail). Gateway derives plan/questions/drafts by folding events. The client folds live events with the same rules — **a golden-fixture contract test** (recorded P0/P1 event logs asserted to produce identical view-models through the TS fold and the Python fold) runs in both CI jobs, so reload never disagrees with the live tail.

### 2.4 CMA resources per run

- **Agent** (once, versioned): `infra/cma/talent-promo-coach.agent.yaml`, applied with `ant beta:agents create/update`. Sessions pin `{id, version}` (eval requirement).
- **Environment** (once): cloud, `networking: unrestricted` (arbitrary job URLs). Environments have delete *and* archive; agents have archive only (terminal). Never auto-archive either.
- **Session** (per run): `resources: [{memory_store (read_write, instructions)}, {file: resume.pdf → /workspace/resume.pdf}?]`; title "«Company» — «Role»"; metadata `{run_id, engine:"cma"}`. The memory mount path is read from the session resource's `mount_path` field, not hardcoded. Console trace URL logged.

---

## 3. CMA agent definition

### 3.1 Config sketch (`talent-promo-coach.agent.yaml`)

```yaml
name: talent-promo-coach
model: claude-opus-4-8
system: |            # the charter — goals & constraints, NOT steps (see §3.3)
  ...
tools:
  - type: agent_toolset_20260401        # bash/read/write/edit/glob/grep/web_search/web_fetch
    default_config: { enabled: true }
  - type: custom
    name: update_plan
    description: >
      Publish or revise your working plan. Call this FIRST, and again whenever
      the plan changes (step added/reordered/done). The user sees this live.
    input_schema:
      type: object
      properties:
        steps:
          type: array
          items: { type: object, properties: { id: {type: string}, title: {type: string},
                   status: {type: string, enum: [pending, active, done, skipped]},
                   note: {type: string} }, required: [id, title, status] }
        current_step_id: { type: string }
      required: [steps]
  - type: custom
    name: ask_user
    description: >
      Ask the candidate ONE thing only they can answer (group tightly-related
      sub-questions into the question text). Use for gap-driven discovery and to
      confirm any claim you cannot ground. The session pauses until they reply.
      [CANONICAL-POLICY: interview rules live in the charter — see §3.3.5]
    input_schema:
      type: object
      properties:
        question: { type: string }        # REQUIRED — the only field reve also carries on the wire
        context:  { type: string }        # why this matters (CMA-only on the wire today)
        kind:     { type: string, enum: [open, confirm, choice] }
        options:  { type: array, items: { type: string } }
      required: [question]
  - type: custom
    name: submit_draft
    description: >
      Submit a resume draft for grounding review. The result contains judge findings;
      address every finding (fix, confirm via ask_user, or cut) and resubmit until
      clean. Do not present a draft with unresolved findings.
    input_schema:
      type: object
      properties:
        draft:   { type: string }         # full draft, markdown — FIELD NAME MATCHES reve's hand
        label:   { type: string }         # e.g. "impact-forward"
        summary: { type: string }
      required: [draft]
# optional experiment: skills: [{type: anthropic, skill_id: docx}]
```

Notes: `submit_draft`'s payload field is **`draft`** (not `text`) — reve's hand schema is `{draft}` and its gate reads `input["draft"]`; the UI fold still accepts `input.draft ?? input.text` defensively. `ask_user`'s extra fields (`context`/`kind`/`options`) are stripped from reve's wire today — the question card renders with them absent, and the reve charter tells the agent to inline options into the question text (P5 may later project the full input; tracked there).

### 3.2 Gateway handling of custom tools

On `session.status_idle` with `requires_action`, the gateway resolves **every** id in `stop_reason.event_ids` (the agent can batch custom tool calls in one turn; the session resumes only when *all* are resolved; a handler keyed to a single pending tool wedges the run):

| Tool | Gateway action | Latency |
|---|---|---|
| `update_plan` | persist plan; **auto-ack** `"ok"` instantly | ms (one round-trip per call — the agent is told to call on change, not per step) |
| `ask_user` | persist question (key + full input), mark run "needs you", hold until answered/skipped → answer text as tool result | human-paced |
| `submit_draft` | persist draft; render JudgeInput from trusted sources (§6.1); `run_judge`; return verdict JSON as tool result; persist verdict **with the exact rendered JudgeInput**; emit a **`gateway.judge_verdict`** event into the browser stream (gateway-owned type — deliberately *not* `span.outcome_evaluation_end`, which has a defined CMA payload contract that a findings verdict doesn't fit and would collide with the Outcomes experiment). Full findings render from the snapshot; the wire event carries a summary `{result: satisfied|needs_revision, explanation, iteration}` mirroring reve's three-field shape. On reve, the adapter synthesizes the same `gateway.judge_verdict` summary from `span.outcome_evaluation_end` (and can enrich by parsing the submit_draft `agent.tool_result` JSON: composite/floor/rationale). | ~10–30 s |

**Plan staleness heuristic (D3):** if ≥N (default 15) model/tool events arrive after the last `update_plan` while a step is `active`, the relay marks the plan stale; the strip dims until the next `update_plan`.

**UI detection rule (engine-agnostic):** plan/question/draft renders key on **tool name** across `agent.custom_tool_use` (CMA) *and* `agent.tool_use` (reve hands): `name ∈ {update_plan, ask_user, submit_draft}` → render from `input`. On reve these must be **built-in hands** (boot factory + schema entry, allow-listed by the pack) — reve has no pack-local hand mechanism, and the MCP route would rename them `mcp__*__update_plan` and break detection; forbidden for these three names.

### 3.3 Charter (system prompt) — content outline

Structured as a **shared core** (engine-neutral wording, byte-identical across engines for the eval — §7.3) plus a **per-engine tool appendix** (CMA: file-tool memory access under the mounted path; reve: memory_view/memory_create hands, one-question-at-a-time).

Shared core:
1. **Identity & prime directive:** career coach; the deliverable is a truthful, *stronger-than-the-candidate-thought-possible* resume; discovery of unrealized assets is the primary value — a rewrite without discovery is failure.
2. **Grounding law** (verbatim from the judge prompt family): resume + user answers + user-confirmed memory claims authorize candidate-facts; JD/research authorize only framing/vocabulary; **your own inferences never authorize a claim** — confirm via ask_user or cut. Escalate unverifiable claims before presenting any draft.
3. **Autonomy & plan discipline:** you decide the steps; publish/maintain the plan via `update_plan`; keep it honest (mark done/skipped as reality changes).
4. **Memory discipline:** read memory before asking anything; write every learned claim/preference/lesson as you go (one claim per file, provenance line, status). On first run, distill `profile/master.md` from the resume. **Write `applications/<slug>/research.md` and `applications/<slug>/gap-analysis.md` before your first `submit_draft`** — they are the judge's research/gap contract inputs. Anything needed for future follow-ups must live in memory, not the sandbox (checkpoints expire). Never store secrets.
5. **Interview craft [CANONICAL-POLICY — this exact text also appears in reve's hand docs]:** gap-driven; every question motivated by a gap or an under-evidenced claim, stating why it matters; group tightly-related sub-questions into one ask; never ask what memory already answers; ~3–7 high-value questions per run; "skip" is an acceptable answer — move on.
6. **Drafting & judge loop:** `submit_draft` (field `draft`), address findings, resubmit; multiple labeled angles welcome (impact-forward / scope-forward / keyword-safe).
7. **Communication:** narrate turning points briefly; final message = outcome summary + what memory learned this run.

---

## 4. UI spec (engine-agnostic layer)

### 4.1 Routes

- `/coach` — intake: resume (paste/upload — reuse ResumeUpload/parse path), job (URL or paste — reuse JobURLInput), engine picker (cma default; reve greyed until live), Start. Behind `NEXT_PUBLIC_COACH_ENABLED`.
- `/coach/run/[runId]` — run screen (§1.2).
- `/coach/memory` — memory browser.
- Existing pages untouched.

### 4.2 Client protocol module (`apps/web/app/lib/engineClient.ts`)

- SSE consumer with **upsert-by-id event map** + gateway cursor resume (replaces AgentEventsStream's flat-envelope logic; the reconnect shell is reusable, the schema is not — the v2 "keep the envelope" ruling is retired, see D2).
- Event fold → view-model `{plan, planStale, feed[], pendingQuestions[], drafts[], verdicts[], status, usage}` — mirrored by the Python snapshot fold, pinned equal by the golden-fixture contract test (§2.3).
- Tolerances baked in: unknown `type`s ignored (`reve.escalation` whitelisted → renders); question card renders with context/kind/options absent; unknown `stop_reason`s → generic "paused"; drafts read `input.draft ?? input.text`; question key = tool id else event id; `event_deltas` previews optional enhancement — buffered `agent.message` stays authoritative.

### 4.3 Component reuse map

| Need | Source | Verdict |
|---|---|---|
| Question card / chat | `QAChat.tsx` (t3.3 branch) | reuse visuals; replace simulated backend + localStorage with run events |
| Diff review of drafts | makeover-branch `page.tsx` (LCS diff, VersionPicker, threads) | **inline functions inside a ~1,300-line monolith — extraction is copy-paste surgery, not a file move; size P2 accordingly** |
| Plan strip | StageStrip (makeover, also inline) | re-skin: data-driven, variable step count, stale state |
| Intake | `/start` page (makeover) + main's upload components | merge into `/coach` |
| Feed | AgentEventsStream shell | new event schema per §4.2 |

### 4.4 Memory layout convention (engine-neutral, **relative paths**)

```
profile/master.md                     # merged narrative profile ("all-around resume" seed)
profile/claims/<slug>.md              # one claim per file: statement, evidence, source
                                      # (resume|answer|inferred), status (user-confirmed|unverified), date
qa/<yyyy-mm-dd>-<slug>.md             # distilled Q&A (not raw transcripts)
applications/<slug>/research.md       # REQUIRED before first submit_draft (judge contract input)
applications/<slug>/gap-analysis.md   # REQUIRED before first submit_draft (judge contract input)
applications/<slug>/notes.md          # per-run retro: targeted, learned, shipped
preferences.md                        # tone, length, do-not-mention list
```

Paths are **relative**: CMA mounts them under `/mnt/memory/<store-slug>/` (read the exact `mount_path` from the session resource); reve's HTTP memory routes 422 absolute paths. Size limits: CMA ≤100KB/memory and **≤2,000 memories per store** (writes fail once full — schedule a prune/consolidate pass, e.g. a periodic consolidation session into a fresh store, before the ceiling); reve renders files with a 4KB advisory budget — the claim-per-file design satisfies both.

---

## 5. Key restrictions & constraints

**CMA platform (verified against live docs 2026-07-12):**
1. **Beta, and the headers split:** session/agent/environment endpoints use `managed-agents-2026-04-01`; **memory-store endpoints use `agent-memory-2026-07-22` — sending both on a memory request is a 400.** A memories-list pagination change lands **2026-07-22** (cursors minted under the old header become invalid). Use an SDK version that knows the split; re-verify surfaces at build time.
2. **First-party API only**; needs `ANTHROPIC_API_KEY` — **absent from `.env` today; owner must provision** (and pick the org/workspace whose Console will show sessions).
3. **SSE no replay** → gateway consolidation mandatory (list+dedupe on every (re)connect); a dropped stream with `ask_user` pending would otherwise deadlock.
4. **Custom tools block the session**; the agent can batch several in one turn — resolve **every** `stop_reason.event_ids` entry; auto-acks must be instant; gateway must survive restarts with questions pending (SQLite).
5. **Memory stores:** attach at session-create only; ≤8/session; ≤100KB/memory; **≤2,000 memories/store** (then writes fail); versions retained ~30 days (export via gateway if long-term audit matters); no restore endpoint (rollback = read version + write back); head versions can't be redacted (overwrite → then redact); never store secrets.
6. **Session mechanics:** creation blocks until resources mount; post-idle status-write race (poll before archive); sandbox checkpoints expire 30 days after last activity (memory is the durable surface); **agents: archive-only and terminal; environments: archive (terminal) + delete (only when unreferenced)** — never auto-archive either.
7. **Rate limits:** 300 RPM create endpoints (agents/sessions/environments), 1,200 RPM read endpoints, per org. The real eval fan-out bound is the org's standard **ITPM/OTPM token limits** — model inference inside sessions draws from them.
8. **Cost:** Opus 4.8 at $5/$25 per MTok; a research+interview+draft session plausibly 200k–800k tokens ≈ **$2–10/run (unvalidated — measure in P0)**. Gateway records spend (simplest source: `sessions.retrieve().usage`), does not enforce v1.
9. **Turn-level events by default** — typewriter streaming only via opt-in `event_deltas` (CMA-only; reve deferred). UI treats previews as enhancement.

**Repo/ops:**
10. **Vercel auto-deploys main** (*per branch docs; owner to confirm in Vercel settings*) — new routes only, env-flagged (D10/D11); commits queued outside 9–5 EDT.
11. **pnpm 8 / lockfile v6** pins in CI — no dependency big-bangs.
12. **New backend deps have three touchpoints:** `anthropic` (+ `aiosqlite` etc.) goes into `apps/api/requirements.txt` (CI installs it, mypy runs over all of apps/api) **and** into `.pre-commit-config.yaml` mypy `additional_dependencies` (currently only fastapi/pydantic/uvicorn — the hook fails otherwise); everything installs into the single repo venv. Eval-only deps stay in `evals/requirements.txt` (never in apps/api — CI rule).
13. **Untracked foundations (D12):** judge/, evals/, docs/ exist on no branch — commit them (queued) before or as part of P0; branch `feat/cma-engine` off `gepa-prep`.
14. **Live-smoke gate** (house rule): every phase ends with a real end-to-end run; both prior engines' worst bugs were invisible to green unit tests.

**reve-side (recorded now so the CMA build doesn't corner us):**
15. Hosted reve is **health-only** until three owner deploy steps (hands app → worker retarget → api secrets); P3 sandbox unbuilt (no code-exec in packs; no session-files surface — exports are CMA-only, §2.3); `caller_token` shown once (persist or the session is send-dead); `budget_tokens` optional (default 100k); one pending question max; ask_user extras stripped on the wire; `update_plan` and any tool-schema change are **reve platform work** (built-in hand factories), not pack content; reve 409s documented in §2.2.

---

## 6. What "utilize memory" means here (explicit, because it's crucial)

- **Recall before asking** — hard charter rule; eval-measured (M5 re-ask rate).
- **Every discovery persists** — answers → claim files (user-confirmed); accepted phrasings → preferences; run retros → application notes.
- **Memory compounds** — run #2 against a different job asks fewer, sharper questions and drafts faster: the observable "career coach that knows you" behavior.
- **User owns it** — `/coach/memory` browse/edit/delete (+ write-then-redact); owner edits confer user-confirmed status.
- **Engine-portable** — the relative-path layout is the contract; CMA mounts it, reve scopes it.

### 6.1 Trust boundary (who may ground what)

The judge trusts its contract inputs, so the inputs must not be authorable by the agent being judged:

- `source_profile` = raw resume text **+ answers as recorded by the gateway** (SQLite ask_user rounds — not the agent's paraphrase) **+ memory claims with status `user-confirmed` only** (confirmed = traced to a recorded answer or an owner edit in `/coach/memory`). Agent-`inferred` claims are excluded from grounding.
- `research_findings` / `gap_analysis` = the contract files (§4.4), which carry framing/vocabulary authority only — a fabricated "fact" there cannot ground a candidate-claim (asymmetric rule). For the **eval**, research_findings are additionally cross-checkable against verbatim web-tool results in the event log.
- The gateway **warns/rejects** `submit_draft` when either contract file renders empty (else the judge reclassifies all research vocabulary as ungrounded and the loop thrashes), and persists the exact rendered JudgeInput alongside every verdict for audit.

---

## 7. Evaluation: CMA vs reve (and vs current engine) — **DESIGN ONLY**

> **Owner ruling (2026-07-12):** eval **implementation is out of scope for this repo** — it lives in the separate `talent-promo-eval` repo (in development). This section is the design the eval repo consumes, and §7.5 is the instrumentation contract this repo must ship so that eval is even possible. All eval/judge **scoring models stay in the OpenAI family**. **No candidate simulator** — the owner answers interview questions personally, which bounds scale (see §7.3).

### 7.1 Design

- **Fixed input set:** K application cases (owner's real resume × real job postings; optionally synthetic personas later — note `evals/datagen` injects grounding *failures* into drafts, it does not generate personas; a persona/sheet generator would be net-new eval-repo work with post-checks mirroring perturb.py's gates). Each case ships a **hidden-facts sheet**: true facts absent from the resume (the discovery target). With the owner answering, the sheet is simply *what the owner knows but the resume omits* — authored per case before the run, so M1 has a denominator.
- **Owner-answered interviews:** the owner answers `ask_user` questions live, from memory/resume truth only. Consequences: runs are human-paced and K stays small; answers are automatically realistic (no leakage machinery needed); every answer is already captured verbatim by the gateway (§7.5), which is what grounding audits need.
- **Paired runs:** same case → all arms; **charter shared-core byte-identical** and the `[CANONICAL-POLICY]` interview text verbatim-identical across CMA tool descriptions and reve hand docs (a preregistered charter/tool-doc diff ships with the report); reve pack `models.default` set to the CMA model (or the delta declared); pinned agent/pack versions; **fresh memory store/scope per (arm, case, lineage)** — never the owner's production store; M5's protocol is cold run #1 → warm run #2 within one lineage only.
- **Judge freeze:** judge model (OpenAI family) + prompt version frozen for the whole eval and recorded on every verdict; each final draft scored median-of-3 judge replicates; judge noise reported separately.

### 7.2 Metrics

| # | Metric | Operationalization |
|---|---|---|
| M1 | **Discovery rate** (primary, preregistered) | Per-fact LLM entailment matcher (OpenAI-family, pinned rubric prompt: fact + draft + transcript → surfaced? / used? / grounded?), human-spot-checked. "Surfaced" credited only when tied to a gateway-logged ask_user round. **Micro-average over facts** (per-case macro secondary). Matcher = eval-repo deliverable. |
| M2 | Grounding quality | **Held-out judge configuration** — different OpenAI model and/or prompt version than the in-loop judge, so no arm trained on the scorer (D7); findings **normalized per 100 words** (the "1–4 findings" prompt prior is length-confounded); a small human-coded slice of Opus drafts estimates off-distribution judge error before deltas are trusted (the judge's gold was manufactured with gpt-5-mini on baseline-style drafts; cross-family probes are excluded by the OpenAI-only rule). |
| M3 | Rubric quality — 4 dims | held-out judge rubric pass |
| M4 | Interview efficiency | questions asked; per-question LLM classification (answerable-from-sheet? which fact?; fishing vs targeted), human spot-checks |
| M5 | Memory utilization | re-ask rate on warm run #2 (LLM duplicate detection vs claims present at ask time); claims written/reused |
| M6 | Process | wall-clock, tokens, $ — `UsageNorm` over CMA 4-field usage / reve `total_tokens` / OpenAI 3-field |
| M7 | Human preference | owner blind-ranks paired drafts, subset |

### 7.3 Fairness caveats & analysis plan

- **In-loop gate asymmetry** (D7): CMA iterates against our judge; reve against its pack gate; baseline against nothing. Held-out scoring (M2/M3) plus reporting both in-loop-final and held-out numbers addresses the train-on-test problem; fully equalizing in-loop gates is a later option (inject a judge callable into reve's boot, or ship a GEPA-winner rubric.md — **no export path exists today**).
- **Tool asymmetry:** CMA web_search/web_fetch vs reve Exa MCP — declared, part of what's compared.
- **Prompt-surface parity:** policy text reaches CMA partly via tool descriptions and reve via charter-embedded hand docs — hence the canonical-policy verbatim rule + preregistered diff (§7.1).
- **Budgets:** reve enforces `budget_tokens` (409 on exhaustion); CMA has none — set reve's generously; report cost as an outcome, not a constraint.
- **Statistics — honest at owner-answered scale:** pair at case level; runs within a case are correlated — never treat them as independent. With the owner answering interviews, K will realistically be **~5–8 cases, R=1–2** — that scale supports **effect sizes + qualitative analysis, not significance claims** (a sign test bottoms out at p=0.0625 at n=5). Report per-case paired deltas with cluster-bootstrap CIs and say plainly when the CI spans zero. If a decision-grade comparison (K≈12–15, R≥3) is ever wanted, it requires automating the interview — a simulator decision deferred to the eval repo.
- `metrics.py`/GEPA machinery scores judge-vs-gold — it is *not* the engine comparison and is not blocked by the LANGSMITH_API_KEY gap.

### 7.4 Baseline arm

Same cases through the current OpenAI/Temporal path (no Q&A, no memory) — loses M1/M5 by construction; confirms the thesis, calibrates cost.

### 7.5 Instrumentation contract (IN scope for this repo — what the eval repo consumes)

The product build must make every run **exportable as evidence**. Per run, the gateway persists and exposes (`GET /api/coach/runs/{id}/export` → one JSON bundle):

1. Full MA-shaped event log (the gateway event cache, ordered, with ids).
2. Every `ask_user` round: question input (full), answer verbatim as recorded by the gateway, timestamps, question key.
3. Every `submit_draft`: draft payload, the **exact rendered JudgeInput**, verdict, judge model + prompt version.
4. Plan history (every `update_plan` payload).
5. `UsageNorm` (tokens + $) and wall-clock; engine + pinned agent/pack version; memory store/scope id.
6. Memory diff hooks: store id + the run's memory writes are recoverable via the memories/versions API (CMA) or memory log (reve).

These land naturally in P1/P3 (they're the same tables the snapshot uses); the export endpoint is the only eval-specific addition.

---

## 8. Build plan (phases; each ends with a live smoke)

| Phase | Deliverable | Gate |
|---|---|---|
| **P0 — Foundations** | **Commit untracked judge/evals/docs (queued per work-hours rule); branch `feat/cma-engine` off gepa-prep (D12).** Owner: **create a dedicated workspace** in the main org + a workspace-scoped ANTHROPIC_API_KEY into `.env`; `ant` CLI auth against it; env + agent YAML applied; memory store created; throwaway script drives one real session end-to-end (web_fetch a job URL → ask_user round-trip → memory write → submit_draft stub) | live transcript in the new workspace's Console; measured $ per smoke |
| **P1 — Gateway** | EngineAdapter + CmaAdapter; SQLite; SSE relay with gateway cursor; custom-tool batch handling (§3.2); judge on submit with trusted-source JudgeInput render (§6.1); snapshot endpoint; deps wired into requirements + pre-commit (§5.12) | CLI harness completes a full run **including restart-with-pending-question and a batched (2-question) turn** |
| **P2 — UI** | `/coach` intake, `/coach/run/[id]` (plan strip + stale state, feed, question card(s), draft view + diff extracted from the makeover monolith), status banner, golden-fixture fold test | owner completes a real application run in the browser |
| **P3 — Memory surface** | `/coach/memory` browser (new beta header!); charter memory-discipline tuning; cold-start behavior; warm run #2 verified | second run demonstrably uses claims from first |
| **P4 — Eval instrumentation (hooks only)** | `GET /api/coach/runs/{id}/export` bundle per §7.5 (event log, Q&A verbatim, rendered JudgeInputs + verdicts, plan history, UsageNorm, versions); **eval implementation itself lives in the `talent-promo-eval` repo** | one exported run bundle round-trips into a scoring script stub |
| ~~P5 — talent-promo-reve~~ | **OUT OF SCOPE (owner ruling 2026-07-13): deferred until CMA proves out in real use; reve is context, not deliverable.** Recorded for whenever it revives: ReveAdapter; reve platform work (built-in `update_plan` hand, optionally project full ask_user input / align submit_draft extras); charter port; `models.default` aligned; the 3 owner deploy steps or a local reve. | — |

---

## 9. Open questions — **all resolved** (owner interview 2026-07-13, rulings in Appendix C)

Only remaining owner *action* (not a question): create the dedicated workspace and put its scoped `ANTHROPIC_API_KEY` in `.env` (the permission classifier blocks me from key operations — P0, step 2).

---

## Appendix A — research provenance

- talent-promo working-tree map, branch inventory, judge/eval assets: 3-agent workflow `cma-spec-research` (2026-07-12); citations inline.
- reve wire shapes & parity: `~/tech/reve/src/reve/api/wire.py`, `service.py`, `pack/schemas.py`, `judge/gate.py`, `harness/loop.py`, `tests/p9/test_wire.py`, `docs/p9-spec.md` (P9 **built**, commit 5ff329d; live gate not yet discharged).
- CMA behavior: Managed Agents reference (claude-api skill) cross-checked against live `platform.claude.com/docs/en/managed-agents/*` during review (memory beta-header split, rate-limit tiers, environment delete, 2,000-memory cap, 30-day versions/checkpoints all come from the live docs).

## Appendix C — owner rulings (2026-07-12)

1. **Memory = CMA memory stores confirmed** (D5) — "we already have the memory there" meant CMA's memory feature; use it.
2. **v1 hosting = local-only confirmed** (D11).
3. **Eval: plan it, don't build it here.** Implementation lives in the separate `talent-promo-eval` repo (in development); this repo ships the §7.5 instrumentation/export contract only. Former P4 (harness) and P6 (decision eval) removed from this repo's build plan.
4. **Eval/judge models stay in the OpenAI family** — no cross-family judge probes; held-out judge = different OpenAI model/prompt version.
5. **No candidate simulator — the owner answers interview questions personally.** Eval scale is therefore owner-paced (~5–8 cases); results reported as effect sizes, not significance claims; simulator decision deferred to the eval repo if decision-grade scale is ever wanted.

Interview rulings (2026-07-13):

6. **Dedicated workspace** in the main org hosts the CMA agent, sessions, and memory store; API key scoped to it.
7. **Vercel confirmed: main auto-deploys to prod** — main must always stay shippable; flag-gated new-routes-only discipline is mandatory.
8. **Scope is CMA only.** reve is context/design-target, not deliverable — former P5 removed from this work; revisit only after CMA proves out in real use.
9. **Export = markdown/text only** (client-side from drafts); docx skill recorded as future option.
10. **Run-export bundle ships as specced** (§7.5) — captured from day one so early real runs remain usable as eval evidence.

## Appendix B — review log (what changed in v2)

4-lens adversarial review (CMA correctness / reve parity / product+repo fit / eval methodology), 47 findings. Blockers fixed: `submit_draft` field renamed to `draft` (reve hand parity); D12 added (judge/evals untracked — branch off gepa-prep); M1 operationalized (entailment matcher, elicited-vs-volunteered, micro-average); §6.1 trust boundary added (agent can no longer author its own grounding evidence). Majors fixed: memory beta-header split + 7/22 pagination migration (§5.1); D7 reworded (gateway-judge is CMA-only; held-out judge for eval); reve ask_user field-stripping + no-tool_use_id + single-pending-question encoded in §2.2/§4.2; `gateway.judge_verdict` replaces the type-squatted synthetic event; D11 hosting decision; abandonment/failure lifecycle (§1.2); research/gap contract files (§4.4 + §6.1); simulator leakage policy; memory isolation per eval lineage; analysis plan + honest K/R sizing; judge distribution-bias probes; prompt-surface parity rule; persona generator scoped as net-new. Minors: rate limits corrected (300/1,200), environments-have-delete, 30-day versions/checkpoints, 2,000-memory cap + mount_path, batched custom-tool resolution, dual-header files.list, `reve.escalation` whitelist, update_plan-as-reve-platform-work, model-parity row, exports CMA-only, pre-commit/CI dep touchpoints, cold-start, fold contract test, desktop-first note, M4/M5 matchers, judge freeze, `session.deleted/updated` tolerance, relative memory paths, charter split, reve 409 semantics, plan staleness, monolith-extraction sizing.
