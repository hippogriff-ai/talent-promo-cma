"""Mock engine — the zero-key scripted golden run (CONTRACT.md §6).

Produces exactly the §6 event script over the same wire vocabulary as CMA.
It is the local dev loop and the source of gateway/tests/fixtures/mock_run.jsonl,
so the script content must stay in sync with those fixtures (regenerate the
fixtures when touching this file — see gateway/tests/test_fold.py).

Blocking semantics mirror CMA: ask_user and submit_draft park the script on a
future keyed by the tool-use event id; adapter.answer(key, content) resumes it.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from tp_gateway.engines.base import RunHandle, RunSpec, UsageNorm, WireEvent

_DONE = object()  # queue sentinel: no more events

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


def _kickoff_text(spec: RunSpec) -> str:
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


class _MockRun:
    def __init__(self, spec: RunSpec, delay_s: float) -> None:
        self.spec = spec
        self.delay_s = delay_s
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
        try:
            # 0. kickoff echo (resume + job) — folds per the §3 kickoff feed rule
            self._emit("user.message", content=[{"type": "text", "text": _kickoff_text(self.spec)}])
            await self._pause()
            # 1. running
            self._emit("session.status_running")
            await self._pause()
            # 2. initial plan
            self._emit(
                "agent.custom_tool_use",
                name="update_plan",
                input=_plan("active", "pending", "pending", "pending", "research"),
            )
            await self._pause()
            # 3. research narration + web_search + span usage
            self._emit(
                "agent.message",
                content=[
                    {
                        "type": "text",
                        "text": "Researching the company and the posting.\n"
                        "I'm mapping the JD's hard requirements against your resume to find the gaps worth asking about.",
                    }
                ],
            )
            await self._pause()
            self._emit(
                "agent.tool_use",
                name="web_search",
                input={"query": "Acme Corp site reliability engineering team culture"},
                tool_use_id="mocktool_001",
            )
            await self._pause()
            self._emit(
                "span.model_request_end",
                model_request_start_id="mockspan_001",
                model_usage={
                    "input_tokens": 1200,
                    "output_tokens": 340,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            )
            await self._pause()
            # 4. plan: research done, interview active
            self._emit(
                "agent.custom_tool_use",
                name="update_plan",
                input=_plan("done", "active", "pending", "pending", "interview"),
            )
            await self._pause()
            # 5–6. ask_user + idle; block until the answer arrives
            ask_id = self._emit(
                "agent.custom_tool_use",
                name="ask_user",
                input={"question": QUESTION_TEXT, "context": QUESTION_CONTEXT, "kind": "open"},
            )
            answer = await self._block_on(ask_id)
            # 7. answer echo + running
            self._emit(
                "user.custom_tool_result",
                custom_tool_use_id=ask_id,
                content=[{"type": "text", "text": answer}],
            )
            await self._pause()
            self._emit("session.status_running")
            await self._pause()
            # 8. what the answer unlocks + plan update
            self._emit(
                "agent.message",
                content=[
                    {
                        "type": "text",
                        "text": "That unlocks the biggest gap.\n"
                        "Your incident-response experience was invisible on paper — I'll surface it as a "
                        "first-class qualification instead of leaving it implied.",
                    }
                ],
            )
            await self._pause()
            self._emit(
                "agent.custom_tool_use",
                name="update_plan",
                input=_plan("done", "done", "active", "pending", "draft"),
            )
            await self._pause()
            # 9–10. first draft -> blocked on judge tool result (stub: needs_revision)
            draft1_id = self._emit(
                "agent.custom_tool_use",
                name="submit_draft",
                input={
                    "draft": _draft(answer, revised=False),
                    "label": "impact-forward",
                    "summary": "Leads with measurable impact; surfaces the incident-response discovery.",
                },
            )
            await self._block_on(draft1_id)
            # 11. revise and resubmit — judged satisfied
            self._emit(
                "agent.message",
                content=[
                    {
                        "type": "text",
                        "text": "Revising the draft.\n"
                        "The review flagged an ungrounded claim — tightening it to what your resume and "
                        "answers actually support.",
                    }
                ],
            )
            await self._pause()
            draft2_id = self._emit(
                "agent.custom_tool_use",
                name="submit_draft",
                input={
                    "draft": _draft(answer, revised=True),
                    "label": "impact-forward (revised)",
                    "summary": "Grounding findings addressed; every claim traces to the resume or your answers.",
                },
            )
            await self._block_on(draft2_id)
            # 12. all done + final summary + end_turn
            self._emit(
                "agent.custom_tool_use",
                name="update_plan",
                input=_plan("done", "done", "done", "done", None),
            )
            await self._pause()
            self._emit(
                "agent.message",
                content=[
                    {
                        "type": "text",
                        "text": "Done — one revised draft delivered.\n"
                        "Discovery: your informal incident-response ownership is now a headline "
                        "qualification. The revised draft passed the grounding review.",
                    }
                ],
            )
            await self._pause()
            self._emit("session.status_idle", stop_reason={"type": "end_turn"})
        except asyncio.CancelledError:
            raise  # interrupt() emits its own idles + sentinel
        else:
            self.finished = True
            self.queue.put_nowait(_DONE)


class MockEngine:
    """Scripted engine; one _MockRun per run_id, held in memory (not restart-safe)."""

    def __init__(self, delay_ms: int) -> None:
        self.delay_s = delay_ms / 1000.0
        self.runs: dict[str, _MockRun] = {}

    async def create_run(self, spec: RunSpec) -> RunHandle:
        run = _MockRun(spec, self.delay_s)
        self.runs[spec.run_id] = run
        run.task = asyncio.create_task(run.script())
        return RunHandle(run_id=spec.run_id, engine="mock", session_id=spec.run_id)

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
