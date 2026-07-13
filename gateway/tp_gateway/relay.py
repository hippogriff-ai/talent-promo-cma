"""RunManager: per-run relay tasks (adapter.events -> SQLite upsert -> SSE
broadcast) and custom-tool dispatch (CONTRACT.md §4).

Dispatch happens on `session.status_idle {requires_action}`: EVERY id in
stop_reason.event_ids is resolved by tool name (CMA batches custom tool calls;
resolving fewer than all wedges the session). Dispatch is DB-idempotent —
answered questions re-send their recorded answer and judged drafts re-send the
stored verdict — so a restarted CMA relay that re-sees historical idles cannot
wedge a run; duplicate sends to already-resolved tools are caught and logged.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from tp_gateway.config import Settings
from tp_gateway.db import Database
from tp_gateway.engines.base import EngineAdapter, RunHandle, RunSpec, WireEvent
from tp_gateway.engines.cma import CmaEngine
from tp_gateway.engines.mock import MockEngine
from tp_gateway.fold import CUSTOM_TOOL_NAMES
from tp_gateway.tools import (
    UPDATE_PLAN_ACK,
    JudgeVerdict,
    judge_draft,
    render_judge_input,
    verdict_tool_result,
    verdict_wire_event,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


class RunManager:
    def __init__(self, settings: Settings, db: Database, cma_engine: CmaEngine | None = None) -> None:
        self.settings = settings
        self.db = db
        self.mock = MockEngine(settings.tp_mock_delay_ms)
        self.cma = cma_engine or CmaEngine(settings)
        self._handles: dict[str, RunHandle] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._dispatched_idles: set[tuple[str, str]] = set()  # (run_id, idle event id), per process

    def adapter(self, engine: str) -> EngineAdapter:
        if engine == "mock":
            return self.mock
        if engine == "cma":
            return self.cma
        raise KeyError(f"unknown engine: {engine}")

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def create_run(
        self,
        engine: str,
        title: str,
        resume_text: str,
        job_text: str,
        job_url: str | None,
    ) -> str:
        run_id = new_run_id()
        agent_ref: dict[str, Any] = {"engine": engine}
        if engine == "cma":
            agent_ref["agent_id"] = self.settings.cma_agent_id
            if self.settings.cma_agent_version is not None:
                agent_ref["agent_version"] = self.settings.cma_agent_version
        self.db.insert_run(run_id, engine, title, resume_text, job_text, job_url, agent_ref, _now_iso())
        spec = RunSpec(run_id=run_id, title=title, resume_text=resume_text, job_text=job_text, job_url=job_url)
        handle = await self.adapter(engine).create_run(spec)
        self.db.set_run_session(run_id, handle.session_id)
        self._handles[run_id] = handle
        self._tasks[run_id] = asyncio.create_task(self._relay(run_id, handle))
        return run_id

    def get_handle(self, run_id: str) -> RunHandle | None:
        handle = self._handles.get(run_id)
        if handle is not None:
            return handle
        run = self.db.get_run(run_id)
        if run and run["engine"] == "cma" and run["session_id"]:
            # restart-safety: CMA handles are just session ids
            handle = RunHandle(run_id=run_id, engine="cma", session_id=run["session_id"])
            self._handles[run_id] = handle
            return handle
        return None  # mock runs do not survive gateway restarts

    def ensure_relay(self, run_id: str) -> None:
        """(Re)start a relay for a CMA run after gateway restart; the relay's
        list-reconciliation upserts everything back into SQLite seamlessly."""
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            return
        handle = self.get_handle(run_id)
        if handle is None or handle.engine != "cma" or not self.settings.cma_configured:
            return
        self._tasks[run_id] = asyncio.create_task(self._relay(run_id, handle))

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: PERF203
                pass
        self._tasks.clear()

    # ── SSE broadcast ────────────────────────────────────────────────────────

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        self._subscribers.get(run_id, set()).discard(q)

    def _publish(self, run_id: str, wire: WireEvent) -> None:
        for q in self._subscribers.get(run_id, set()):
            q.put_nowait(wire)

    def emit_gateway_event(self, run_id: str, event: dict[str, Any]) -> None:
        seq, _ = self.db.upsert_event(run_id, event)
        self._publish(run_id, {"seq": seq, **event})

    # ── relay loop ───────────────────────────────────────────────────────────

    async def _relay(self, run_id: str, handle: RunHandle) -> None:
        adapter = self.adapter(handle.engine)
        try:
            async for ev in adapter.events(handle):
                if not ev.get("id") or not ev.get("type"):
                    continue
                seq, _inserted = self.db.upsert_event(run_id, ev)
                wire = {"seq": seq, **{k: v for k, v in ev.items() if k != "seq"}}
                self._publish(run_id, wire)
                try:
                    await self._react(run_id, handle, adapter, wire)
                except Exception:
                    logger.exception("relay dispatch failed (run %s, event %s)", run_id, ev.get("id"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("relay crashed (run %s)", run_id)

    async def _react(self, run_id: str, handle: RunHandle, adapter: EngineAdapter, wire: WireEvent) -> None:
        t = wire["type"]
        is_custom = t == "agent.custom_tool_use" or (
            t in ("agent.tool_use", "agent.mcp_tool_use") and wire.get("name") in CUSTOM_TOOL_NAMES
        )
        if is_custom:
            self._persist_tool_use(run_id, wire)
        elif t == "session.status_idle":
            stop = wire.get("stop_reason") or {}
            if stop.get("type") == "requires_action":
                idle_key = (run_id, str(wire["id"]))
                if idle_key not in self._dispatched_idles:
                    self._dispatched_idles.add(idle_key)
                    await self._dispatch_requires_action(run_id, handle, adapter, stop.get("event_ids") or [])

    def _persist_tool_use(self, run_id: str, wire: WireEvent) -> None:
        name = wire.get("name")
        tool_input = wire.get("input") or {}
        key = str(wire.get("tool_use_id") or wire["id"])
        seq = int(wire["seq"])
        if name == "update_plan":
            self.db.upsert_plan(run_id, seq, tool_input.get("steps") or [], tool_input.get("current_step_id"))
        elif name == "ask_user":
            self.db.upsert_question(
                run_id,
                key,
                tool_input.get("question", ""),
                tool_input.get("context"),
                tool_input.get("kind"),
                list(tool_input["options"]) if tool_input.get("options") else None,
                seq,
                _now_iso(),
            )
        elif name == "submit_draft":
            self.db.upsert_draft(
                run_id,
                key,
                tool_input.get("label") or "draft",
                tool_input.get("summary"),
                tool_input.get("draft") or tool_input.get("text") or "",
                seq,
            )

    # ── requires_action dispatch (§4) ────────────────────────────────────────

    async def _dispatch_requires_action(
        self, run_id: str, handle: RunHandle, adapter: EngineAdapter, event_ids: list[str]
    ) -> None:
        for eid in event_ids:
            tool_ev = self.db.get_event_by_id(run_id, eid)
            if tool_ev is None:
                logger.warning("idle references unknown event %s (run %s)", eid, run_id)
                continue
            name = tool_ev.get("name")
            key = str(tool_ev.get("tool_use_id") or eid)
            if name == "update_plan":
                await self._safe_answer(adapter, handle, key, UPDATE_PLAN_ACK)
            elif name == "ask_user":
                q = self.db.get_question(run_id, key)
                if q is not None and q["answer"] is not None:
                    # restart recovery: answer recorded but possibly never delivered
                    await self._safe_answer(adapter, handle, key, q["answer"])
                # else: hold — resolved later via POST .../answers
            elif name == "submit_draft":
                await self._judge_and_answer(run_id, handle, adapter, key, tool_ev.get("input") or {})
            else:
                logger.warning("unhandled blocking tool %r (run %s, event %s)", name, run_id, eid)

    async def _judge_and_answer(
        self, run_id: str, handle: RunHandle, adapter: EngineAdapter, draft_id: str, tool_input: dict[str, Any]
    ) -> None:
        existing = self.db.get_verdict(run_id, draft_id)
        if existing is not None:
            # already judged (restart recovery): re-send the stored verdict
            payload = verdict_tool_result(
                existing["result"], existing["explanation"], existing["findings"], existing["rubric"]
            )
            await self._safe_answer(adapter, handle, draft_id, payload)
            return

        run = self.db.get_run(run_id)
        assert run is not None
        draft_text = tool_input.get("draft") or tool_input.get("text") or ""
        research = tool_input.get("research_notes")
        gap = tool_input.get("gap_analysis")
        if run["engine"] == "cma" and (not research or not gap):
            try:
                mem_research, mem_gap = await self.cma.read_contract_files()
                research = research or mem_research
                gap = gap or mem_gap
            except Exception:
                logger.exception("memory contract-file read failed (run %s)", run_id)
        judge_input = render_judge_input(
            run["resume_text"], self.db.list_questions(run_id), run["job_text"], research, gap, draft_text
        )
        iteration = self.db.count_verdicts(run_id) + 1
        verdict: JudgeVerdict = await judge_draft(self.settings, judge_input, iteration)
        self.db.insert_verdict(
            run_id,
            draft_id,
            verdict.result,
            verdict.explanation,
            verdict.iteration,
            verdict.findings,
            verdict.rubric,
            verdict.judge_input,
            verdict.judge_model,
            verdict.prompt_version,
            _now_iso(),
        )
        # verdict event goes on the wire BEFORE the tool result resumes the engine
        self.emit_gateway_event(run_id, verdict_wire_event(draft_id, verdict, _now_iso()))
        await self._safe_answer(
            adapter,
            handle,
            draft_id,
            verdict_tool_result(verdict.result, verdict.explanation, verdict.findings, verdict.rubric),
        )

    async def _safe_answer(self, adapter: EngineAdapter, handle: RunHandle, key: str, content: str) -> None:
        try:
            await adapter.answer(handle, key, content)
        except Exception:
            # e.g. duplicate resolution after a restart re-dispatch — engine 4xxs, run unaffected
            logger.exception("tool-result send failed (run %s, key %s)", handle.run_id, key)
