"""Mock engine — zero-key scripted runs (CONTRACT.md §6 mock, §8 mock-long).

The scripted-run MACHINERY here is shared; scenario CONTENT is data. A script
is a flat list of ops interpreted by _MockRun.script():

    KICKOFF                       user.message echoing resume+job (§3 kickoff feed rule)
    PAUSE                         sleep TP_MOCK_DELAY_MS
    ("emit", <type>, <payload>)   payload: dict, or callable(ctx) -> dict
    ("ask", <input>)              ask_user + idle(requires_action); BLOCKS until the
                                  answer arrives, appends it to ctx.answers, then echoes
                                  it as user.custom_tool_result
    ("submit", <input>)           submit_draft + idle; BLOCKS on the judge tool result

Two scenarios exist: MOCK_SCRIPT below (engine "mock", the minimal golden run —
its content must stay byte-stable: gateway/tests/fixtures/mock_run.jsonl and the
web e2e stub depend on it) and mock_long.LONG_SCRIPT (engine "mock-long", the
realistic-scale run; imported lazily to avoid a module cycle).

Blocking semantics mirror CMA: ask_user and submit_draft park the script on a
future keyed by the tool-use event id; adapter.answer(key, content) resumes it.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Union

from tp_gateway.engines.base import RunHandle, RunSpec, UsageNorm, WireEvent

_DONE = object()  # queue sentinel: no more events

# ── script ops (shared machinery; scenarios are lists of these) ──────────────


@dataclass
class ScriptContext:
    """Mutable per-run state a scenario's callable payloads can read."""

    spec: RunSpec
    answers: list[str] = field(default_factory=list)  # ask_user answers, in ask order


Payload = Union[dict[str, Any], Callable[[ScriptContext], dict[str, Any]]]
Op = tuple  # ("emit", type, Payload) | ("pause",) | ("kickoff",) | ("ask", Payload) | ("submit", Payload)

PAUSE: Op = ("pause",)
KICKOFF: Op = ("kickoff",)


def msg(text: str) -> Op:
    """agent.message op (headline = first line, body = rest — fold §3)."""
    return ("emit", "agent.message", {"content": [{"type": "text", "text": text}]})


def tool(name: str, tool_input: dict[str, Any], tool_use_id: str) -> Op:
    """Plain agent.tool_use op (folds to a collapsed tool feed item)."""
    return ("emit", "agent.tool_use", {"name": name, "input": tool_input, "tool_use_id": tool_use_id})


def span(start_id: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_creation: int = 0) -> Op:
    return (
        "emit",
        "span.model_request_end",
        {
            "model_request_start_id": start_id,
            "model_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        },
    )


def update_plan(plan_input: dict[str, Any]) -> Op:
    return ("emit", "agent.custom_tool_use", {"name": "update_plan", "input": plan_input})


def _resolve(payload: Payload, ctx: ScriptContext) -> dict[str, Any]:
    return payload(ctx) if callable(payload) else payload


def kickoff_text(spec: RunSpec) -> str:
    """The run-inputs echo (resume + job), mirroring the CMA kickoff. The fold
    swallows it into the §3 kickoff feed item; the UI reads Snapshot.inputs."""
    parts = [
        "New application run — inputs below.",
        "",
        "## CANDIDATE RESUME (verbatim)",
        "",
        spec.resume_text,
        "",
        "## TARGET JOB",
        "",
    ]
    if spec.job_url:
        parts.append(f"Posting URL: {spec.job_url}")
    if spec.job_text:
        parts.append(spec.job_text)
    return "\n".join(parts)


# ── the minimal scenario (engine "mock") — golden-fixture source, keep stable ─

QUESTION_TEXT = (
    "The job description leans heavily on production incident response, but your resume "
    "never mentions on-call or incident work. Have you ever owned production incidents — "
    "even informally (side projects, or being the person everyone pinged when things broke)?"
)
QUESTION_CONTEXT = (
    "The posting lists 'owns incident response end-to-end' as a core requirement; this is "
    "the biggest gap between the JD and your resume."
)


def _draft(answer: str, revised: bool) -> str:
    summary_line = (
        "Backend engineer with six years designing, shipping, and operating distributed services."
        if not revised
        else "Backend engineer (six years, per resume) who designs, ships, and operates distributed services."
    )
    return f"""# Jordan Rivera

{summary_line}

## Selected experience

- Led the billing pipeline migration to an event-driven architecture, cutting p95 latency 40%.
- Incident response: {answer}
- Ran weekly production-readiness reviews and mentored two junior engineers.

## Skills

Python, Go, Kubernetes, Terraform, PostgreSQL, Prometheus/Grafana.
"""


def _plan(research: str, interview: str, draft: str, deliver: str, current: str | None) -> dict:
    return {
        "steps": [
            {"id": "ingest", "title": "Ingest resume & job posting", "status": "done"},
            {"id": "research", "title": "Research the company & JD", "status": research},
            {"id": "interview", "title": "Gap-driven discovery interview", "status": interview},
            {"id": "draft", "title": "Draft & revise the resume", "status": draft},
            {"id": "deliver", "title": "Deliver final versions", "status": deliver},
        ],
        "current_step_id": current,
    }


# §6 event script, verbatim — content changes here invalidate the golden fixtures.
MOCK_SCRIPT: list[Op] = [
    # 0. kickoff echo (resume + job) — folds per the §3 kickoff feed rule
    KICKOFF,
    PAUSE,
    # 1. running
    ("emit", "session.status_running", {}),
    PAUSE,
    # 2. initial plan
    update_plan(_plan("active", "pending", "pending", "pending", "research")),
    PAUSE,
    # 3. research narration + web_search + span usage
    msg(
        "Researching the company and the posting.\n"
        "I'm mapping the JD's hard requirements against your resume to find the gaps worth asking about."
    ),
    PAUSE,
    tool("web_search", {"query": "Acme Corp site reliability engineering team culture"}, "mocktool_001"),
    PAUSE,
    span("mockspan_001", 1200, 340),
    PAUSE,
    # 4. plan: research done, interview active
    update_plan(_plan("done", "active", "pending", "pending", "interview")),
    PAUSE,
    # 5–7. ask_user + idle; block until the answer arrives; echo it + running
    ("ask", {"question": QUESTION_TEXT, "context": QUESTION_CONTEXT, "kind": "open"}),
    PAUSE,
    ("emit", "session.status_running", {}),
    PAUSE,
    # 8. what the answer unlocks + plan update
    msg(
        "That unlocks the biggest gap.\n"
        "Your incident-response experience was invisible on paper — I'll surface it as a "
        "first-class qualification instead of leaving it implied."
    ),
    PAUSE,
    update_plan(_plan("done", "done", "active", "pending", "draft")),
    PAUSE,
    # 9–10. first draft -> blocked on judge tool result (stub: needs_revision)
    (
        "submit",
        lambda ctx: {
            "draft": _draft(ctx.answers[0], revised=False),
            "label": "impact-forward",
            "summary": "Leads with measurable impact; surfaces the incident-response discovery.",
        },
    ),
    # 11. revise and resubmit — judged satisfied
    msg(
        "Revising the draft.\n"
        "The review flagged an ungrounded claim — tightening it to what your resume and "
        "answers actually support."
    ),
    PAUSE,
    (
        "submit",
        lambda ctx: {
            "draft": _draft(ctx.answers[0], revised=True),
            "label": "impact-forward (revised)",
            "summary": "Grounding findings addressed; every claim traces to the resume or your answers.",
        },
    ),
    # 12. all done + final summary + end_turn
    update_plan(_plan("done", "done", "done", "done", None)),
    PAUSE,
    msg(
        "Done — one revised draft delivered.\n"
        "Discovery: your informal incident-response ownership is now a headline "
        "qualification. The revised draft passed the grounding review."
    ),
    PAUSE,
    ("emit", "session.status_idle", {"stop_reason": {"type": "end_turn"}}),
]


def script_for(engine: str) -> list[Op]:
    """Scenario selection by engine string. mock_long is imported lazily so the
    scenario module can reuse the op helpers above without a cycle."""
    if engine == "mock-long":
        from tp_gateway.engines.mock_long import LONG_SCRIPT

        return LONG_SCRIPT
    return MOCK_SCRIPT


# ── shared machinery ─────────────────────────────────────────────────────────


class _MockRun:
    def __init__(self, spec: RunSpec, delay_s: float, ops: list[Op]) -> None:
        self.spec = spec
        self.delay_s = delay_s
        self.ops = ops
        self.queue: asyncio.Queue = asyncio.Queue()
        self.waiters: dict[str, asyncio.Future] = {}
        self.n = 0
        self.usage_in = 0
        self.usage_out = 0
        self.finished = False
        self.task: asyncio.Task | None = None

    def _emit(self, event_type: str, **payload) -> str:
        self.n += 1
        eid = f"mockevt_{self.n:03d}"
        ev: WireEvent = {
            "id": eid,
            "type": event_type,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if event_type == "span.model_request_end":
            mu = payload.get("model_usage") or {}
            self.usage_in += mu.get("input_tokens", 0)
            self.usage_out += mu.get("output_tokens", 0)
        self.queue.put_nowait(ev)
        return eid

    async def _pause(self) -> None:
        await asyncio.sleep(self.delay_s)

    async def _block_on(self, event_id: str) -> str:
        """Emit idle(requires_action) for event_id and wait for its tool result."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.waiters[event_id] = fut
        self._emit("session.status_idle", stop_reason={"type": "requires_action", "event_ids": [event_id]})
        return await fut

    async def script(self) -> None:
        ctx = ScriptContext(spec=self.spec)
        try:
            for op in self.ops:
                kind = op[0]
                if kind == "pause":
                    await self._pause()
                elif kind == "kickoff":
                    self._emit(
                        "user.message",
                        content=[{"type": "text", "text": kickoff_text(self.spec)}],
                    )
                elif kind == "emit":
                    self._emit(op[1], **_resolve(op[2], ctx))
                elif kind == "ask":
                    ask_id = self._emit("agent.custom_tool_use", name="ask_user", input=_resolve(op[1], ctx))
                    answer = await self._block_on(ask_id)
                    ctx.answers.append(answer)
                    self._emit(
                        "user.custom_tool_result",
                        custom_tool_use_id=ask_id,
                        content=[{"type": "text", "text": answer}],
                    )
                elif kind == "submit":
                    draft_id = self._emit(
                        "agent.custom_tool_use", name="submit_draft", input=_resolve(op[1], ctx)
                    )
                    await self._block_on(draft_id)  # judge tool result resumes us
                else:  # pragma: no cover — scenario authoring error
                    raise ValueError(f"unknown script op: {op!r}")
        except asyncio.CancelledError:
            raise  # interrupt() emits its own idles + sentinel
        else:
            self.finished = True
            self.queue.put_nowait(_DONE)


class MockEngine:
    """Scripted engine; one _MockRun per run_id, held in memory (not restart-safe).
    Serves both mock scenarios — the script is chosen by RunSpec.engine."""

    def __init__(self, delay_ms: int) -> None:
        self.delay_s = delay_ms / 1000.0
        self.runs: dict[str, _MockRun] = {}

    async def create_run(self, spec: RunSpec) -> RunHandle:
        run = _MockRun(spec, self.delay_s, script_for(spec.engine))
        self.runs[spec.run_id] = run
        run.task = asyncio.create_task(run.script())
        return RunHandle(run_id=spec.run_id, engine=spec.engine, session_id=spec.run_id)

    async def events(self, h: RunHandle, cursor: str | None = None) -> AsyncIterator[WireEvent]:
        run = self.runs.get(h.run_id)
        if run is None:
            return
        while True:
            ev = await run.queue.get()
            if ev is _DONE:
                return
            yield ev

    async def send_message(self, h: RunHandle, text: str) -> None:
        run = self._run(h)
        run._emit("user.message", content=[{"type": "text", "text": text}])
        run._emit(
            "agent.message",
            content=[{"type": "text", "text": "Noted — I'll fold that into the current step."}],
        )

    async def answer(self, h: RunHandle, question_key: str, content: str) -> None:
        run = self._run(h)
        fut = run.waiters.pop(question_key, None)
        if fut is not None and not fut.done():
            fut.set_result(content)
        # unknown keys (e.g. re-sent acks) are tolerated: the script isn't waiting

    async def interrupt(self, h: RunHandle) -> None:
        run = self._run(h)
        if run.finished:
            return
        if run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
        # §6: interrupt → idle interrupted → done (end_turn folds to done)
        run._emit("session.status_idle", stop_reason={"type": "interrupted"})
        run._emit("session.status_idle", stop_reason={"type": "end_turn"})
        run.finished = True
        run.queue.put_nowait(_DONE)

    async def usage(self, h: RunHandle) -> UsageNorm:
        run = self._run(h)
        return {
            "input_tokens": run.usage_in,
            "output_tokens": run.usage_out,
            "total_tokens": run.usage_in + run.usage_out,
            "usd": None,
        }

    def _run(self, h: RunHandle) -> _MockRun:
        run = self.runs.get(h.run_id)
        if run is None:
            raise KeyError(f"unknown mock run: {h.run_id} (mock runs do not survive gateway restarts)")
        return run
